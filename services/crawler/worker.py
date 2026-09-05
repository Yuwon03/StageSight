"""
StageSight listing crawler — a separate long-running process from the API.

    cd services/agent && .venv/bin/python ../crawler/worker.py            # loop
    ... --once                                       # single pass
    ... --providers hourplace                        # just one source
    ... --public-data-csv ~/Downloads/촬영지.csv     # load the open dataset
    ... --dedupe-only                                # re-merge, fetch nothing

It shares services/agent/data/catalog.db with the API server (SQLite in WAL
mode), so the API never blocks on a crawl and users never wait for one.

Multi-source since the catalogue outgrew hourplace. The pass logic below is
provider-agnostic: a source declares how to enumerate and fetch its listings
(services/crawler/providers/) and the worker schedules, normalises, delists and
revisions all of them the same way.

Two invariants that predate this and still hold, now per provider:

  * Only ids a pass actually looked for can be delisted, so an interrupted or
    rate-limited pass can never wipe the catalogue.
  * Delisting is scoped to one provider's own ids. A source that fails, or is
    switched off, must never mark another source's listings as gone.

A third was added with the second source: a provider whose rights basis is
`PENDING_PERMISSION` is refused before any request is made. Being able to fetch
a page is not permission to republish it.
"""
import argparse
import asyncio
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The crawler is its own process, so it does not inherit the API's dotenv load.
# Providers read their keys at construction time in the registry import below,
# so this has to happen first or every keyed source reports itself unconfigured.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import store  # noqa: E402
from providers import Rights, enabled_providers, stamp  # noqa: E402
from providers.dedupe import assign_canonical  # noqa: E402
from providers.public_data import PublicDataProvider  # noqa: E402
from providers.registry import PROVIDERS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawler")

DEFAULT_INTERVAL = 1800   # 30 minutes
NEW_PER_PASS = 250        # detail fetches for unseen ids
REFRESH_PER_PASS = 120    # re-checks of listings we already hold
CONCURRENCY = 6


async def run_provider(provider, client, sem) -> dict:
    """One pass over one source."""
    name = provider.name
    prefix = provider.id_prefix
    summary = {"provider": name, "added": 0, "updated": 0, "delisted": 0, "checked": 0}

    store.set_provider_state(name, status="running", rights_status=provider.rights)

    # Bulk preparation (sitemaps, a CSV, an API page walk) happens once.
    if hasattr(provider, "prepare"):
        await provider.prepare(client)
    reason = getattr(provider, "unavailable_reason", None)
    if reason:
        logger.warning(f"[{name}] {reason}")
        store.set_provider_state(name, status="unconfigured", last_error=reason, touch_crawl=True)
        return summary

    source_ids = await provider.discover_ids(client)
    logger.info(f"[{name}] source lists {len(source_ids)} ids")
    if not source_ids:
        store.set_provider_state(name, status="empty", touch_crawl=True)
        return summary

    known = store.ids_for_provider(name)

    # `source_ids` stays the full index — delisting below is derived from it.
    # Only the fetch list is narrowed, to ids that can yield a usable row.
    worth_fetching = getattr(provider, "should_fetch", lambda _sid: True)
    fetchable = [i for i in source_ids if worth_fetching(i)]

    unseen = [i for i in fetchable if f"{prefix}{i}" not in known]
    targets = unseen[:NEW_PER_PASS]
    # Rotate through what we already hold so edits and removals surface.
    live_known = [i for i in fetchable if f"{prefix}{i}" in known]
    random.shuffle(live_known)
    targets += live_known[:REFRESH_PER_PASS]
    logger.info(
        f"[{name}] holds {len(known)}; {len(fetchable)}/{len(source_ids)} fetchable; "
        f"fetching {len(targets)} ({len(unseen)} never seen)"
    )

    fetched, looked_for, found = [], set(), set()

    async def one(sid: str):
        async with sem:
            try:
                raw = await provider.fetch_listing(client, sid)
            except Exception:
                return sid, None
            if raw is None:
                return sid, None
            # Politeness delay only for sources we fetch over the network.
            if not isinstance(provider, PublicDataProvider):
                await asyncio.sleep(0.15)
            try:
                loc = provider.normalize(raw)
            except Exception:
                logger.debug(f"[{name}] normalize failed for {sid}", exc_info=True)
                return sid, None
            if loc is None:
                return sid, None
            return sid, stamp(loc, provider, sid, provider.source_url(sid))

    for start in range(0, len(targets), CONCURRENCY * 6):
        chunk = targets[start : start + CONCURRENCY * 6]
        for res in await asyncio.gather(*[one(s) for s in chunk], return_exceptions=True):
            if isinstance(res, Exception):
                continue
            sid, loc = res
            looked_for.add(f"{prefix}{sid}")
            if loc:
                fetched.append(loc)
                found.add(f"{prefix}{sid}")

    summary["checked"] = len(looked_for)
    if fetched:
        summary["added"], summary["updated"] = store.upsert_many(fetched)

    # Ids the source dropped from its own index are gone. Scoped to this
    # provider's prefix so one source can never delist another's listings.
    gone_from_index = known - {f"{prefix}{i}" for i in source_ids}
    summary["delisted"] = store.mark_absent(found, looked_for | gone_from_index)

    store.set_provider_state(name, status="idle", touch_crawl=True, last_error="")
    logger.info(
        f"[{name}] +{summary['added']} new, ~{summary['updated']} updated, "
        f"-{summary['delisted']} delisted"
    )
    return summary


def run_dedupe() -> int:
    """Link listings that are the same physical venue across providers.

    Runs over the whole catalogue rather than the pass's rows: a new listing on
    one platform can be the twin of one fetched months ago.
    """
    rows = []
    with store.connect() as c:
        for r in c.execute(
            "SELECT id, provider, data, lat, lon FROM locations WHERE delisted_at IS NULL"
        ):
            import json as _json

            d = _json.loads(r["data"])
            addr = ""
            for cit in d.get("citations") or []:
                if cit.get("excerpt"):
                    addr = cit["excerpt"].split("·")[0].strip()
                    break
            rows.append({
                "id": r["id"],
                "provider": r["provider"],
                "name": d.get("name", ""),
                "address": addr,
                "latitude": r["lat"],
                "longitude": r["lon"],
            })

    mapping = assign_canonical(rows)
    if not mapping:
        return 0
    changed = 0
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            for rid, canonical in mapping.items():
                cur = c.execute("SELECT canonical_id FROM locations WHERE id=?", (rid,)).fetchone()
                if cur and cur["canonical_id"] != canonical:
                    c.execute("UPDATE locations SET canonical_id=? WHERE id=?", (canonical, rid))
                    changed += 1
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return changed


async def run_pass(providers) -> dict:
    started = datetime.now(timezone.utc)
    store.set_meta("crawl_status", "running")
    totals = {"added": 0, "updated": 0, "delisted": 0, "checked": 0, "pruned": 0, "merged": 0}

    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    try:
        async with httpx.AsyncClient(
            limits=limits, timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True
        ) as client:
            sem = asyncio.Semaphore(CONCURRENCY)
            for provider in providers:
                try:
                    s = await run_provider(provider, client, sem)
                except Exception as e:
                    # One source failing must not abort the others.
                    logger.exception(f"[{provider.name}] pass failed")
                    store.set_provider_state(
                        provider.name, status="error", last_error=str(e)[:300], touch_crawl=True
                    )
                    continue
                for k in ("added", "updated", "delisted", "checked"):
                    totals[k] += s[k]

        totals["pruned"] = store.prune_tombstones()
        totals["merged"] = run_dedupe()
    except Exception as e:
        logger.exception("crawl pass failed")
        store.set_meta("crawl_status", f"error: {e}")
        return totals
    finally:
        store.set_meta("last_crawl", datetime.now(timezone.utc).isoformat())

    store.set_meta("crawl_status", "idle")
    took = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        f"pass done in {took:.0f}s — +{totals['added']} new, ~{totals['updated']} updated, "
        f"-{totals['delisted']} delisted, {totals['pruned']} pruned, "
        f"{totals['merged']} merged, rev={store.current_rev()}"
    )
    return totals


async def main() -> None:
    ap = argparse.ArgumentParser(description="StageSight multi-source listing crawler")
    ap.add_argument("--once", action="store_true", help="run a single pass and exit")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between passes")
    ap.add_argument("--providers", help="comma-separated provider names (default: all enabled)")
    ap.add_argument("--public-data-csv", help="path to a data.go.kr 촬영지 CSV")
    ap.add_argument("--public-data-dataset", help="data.go.kr dataset id (default 15052437)")
    ap.add_argument("--dedupe-only", action="store_true", help="re-merge venues, fetch nothing")
    ap.add_argument("--list-providers", action="store_true", help="show sources and their status")
    args = ap.parse_args()

    store.init_db()

    if args.list_providers:
        from providers.registry import roadmap

        for r in roadmap():
            mark = "ON " if r["enabled"] else "off"
            print(f"  [{mark}] {r['provider']:12} {r['rights_status']:18} {r['label']}")
            if r.get("blocked_on"):
                print(f"          blocked on: {r['blocked_on']}")
        return

    if args.public_data_csv or args.public_data_dataset:
        PROVIDERS["public_data"] = PublicDataProvider(
            csv_path=args.public_data_csv, dataset=args.public_data_dataset
        )

    only = [p.strip() for p in args.providers.split(",")] if args.providers else None
    providers = enabled_providers(only)
    if only:
        refused = [n for n in only if n not in {p.name for p in providers}]
        for n in refused:
            logger.warning(f"[{n}] refused — rights basis is {Rights.PENDING_PERMISSION}")

    logger.info(f"store: {store.DB_PATH} (holds {store.count()} live listings)")
    logger.info(f"providers: {', '.join(p.name for p in providers) or '(none)'}")

    if args.dedupe_only:
        logger.info(f"merged {run_dedupe()} listings into shared venues")
        return

    if args.once:
        await run_pass(providers)
        return

    while True:
        await run_pass(providers)
        logger.info(f"sleeping {args.interval}s until next pass")
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("stopped")
