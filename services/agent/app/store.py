"""
SQLite-backed listing store, shared by the API server and the crawler process.

Why a database and not the JSON blob we started with: two processes now write to
the catalog, clients need to ask "what changed since revision N", and delisted
rows must be reclaimed so the store does not grow forever. None of that is
workable with a whole-file rewrite.

Concurrency: WAL mode, so the crawler can write while the API reads.

Revisions: every insert/update/delete stamps a row with the next value of a
single global counter. A client remembers the highest revision it has seen and
asks for everything above it, which is all the delta sync needs — no timestamps
to keep in sync across processes, no clock skew.
"""
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.models.korean_locations import KoreanLocation

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
# Overridable so tests (and any throwaway run) never touch the real catalog —
# the test fixtures truncate the table, which once wiped production data.
DB_PATH = Path(os.environ.get("STAGESIGHT_DB_PATH") or DATA_DIR / "catalog.db")

# How long a freshly-seen listing carries the "new" badge.
NEW_WINDOW = timedelta(hours=72)
# How long a delisted row is kept so slow clients still learn it disappeared.
TOMBSTONE_RETENTION = timedelta(days=30)
# Consecutive crawls a listing must be missing before we call it delisted. One
# missed fetch is usually a transient error, not a removal.
MISS_THRESHOLD = 2

_local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@contextmanager
def connect():
    """One connection per thread; SQLite objects are not shareable across them."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    yield conn


def init_db() -> None:
    with connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS locations (
                id          TEXT PRIMARY KEY,
                data        TEXT NOT NULL,        -- KoreanLocation as JSON
                first_seen  TEXT NOT NULL,        -- drives the "new" badge
                last_seen   TEXT NOT NULL,
                delisted_at TEXT,                 -- set when it drops off the source
                miss_count  INTEGER NOT NULL DEFAULT 0,
                rev         INTEGER NOT NULL,
                region      TEXT,
                category    TEXT,
                price       INTEGER NOT NULL DEFAULT 0,
                window_dir  TEXT,
                parking     INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_loc_rev      ON locations(rev);
            CREATE INDEX IF NOT EXISTS idx_loc_delisted ON locations(delisted_at);
            CREATE INDEX IF NOT EXISTS idx_loc_filter   ON locations(category, region, price);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO meta(key, value) VALUES ('rev', '0');
            INSERT OR IGNORE INTO meta(key, value) VALUES ('last_crawl', '');
            INSERT OR IGNORE INTO meta(key, value) VALUES ('crawl_status', 'idle');

            -- One row per source. Crawl state has to be per-provider or a
            -- provider that is rate-limited, unapproved or simply slow drags
            -- the reported status of every other one with it.
            CREATE TABLE IF NOT EXISTS provider_state (
                provider     TEXT PRIMARY KEY,
                status       TEXT NOT NULL DEFAULT 'idle',
                last_crawl   TEXT,
                last_error   TEXT,
                listings     INTEGER NOT NULL DEFAULT 0,
                rights_status TEXT NOT NULL DEFAULT 'robots_allowed',
                enabled      INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        _migrate(c)


# Columns added after the catalogue went multi-source. SQLite has no
# ADD COLUMN IF NOT EXISTS, so the existing columns are read first — this runs
# on every start and must stay cheap and idempotent.
_ADDED_COLUMNS = {
    "provider": "TEXT NOT NULL DEFAULT 'hourplace'",
    "listing_kind": "TEXT NOT NULL DEFAULT 'bookable'",
    "canonical_id": "TEXT",
    "lat": "REAL",
    "lon": "REAL",
}


def _migrate(c: sqlite3.Connection) -> None:
    have = {r["name"] for r in c.execute("PRAGMA table_info(locations)")}
    for name, decl in _ADDED_COLUMNS.items():
        if name not in have:
            c.execute(f"ALTER TABLE locations ADD COLUMN {name} {decl}")
    c.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_loc_provider  ON locations(provider);
        CREATE INDEX IF NOT EXISTS idx_loc_kind      ON locations(listing_kind);
        CREATE INDEX IF NOT EXISTS idx_loc_canonical ON locations(canonical_id);
        """
    )


def _next_rev(c: sqlite3.Connection) -> int:
    row = c.execute("SELECT value FROM meta WHERE key='rev'").fetchone()
    nxt = int(row["value"]) + 1
    c.execute("UPDATE meta SET value=? WHERE key='rev'", (str(nxt),))
    return nxt


def current_rev() -> int:
    with connect() as c:
        row = c.execute("SELECT value FROM meta WHERE key='rev'").fetchone()
        return int(row["value"]) if row else 0


def get_meta(key: str) -> str:
    with connect() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""


def set_meta(key: str, value: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ── Serialisation ───────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """A listing as the API returns it: stored JSON plus derived freshness."""
    data = json.loads(row["data"])
    first_seen = _parse(row["first_seen"])
    is_new = bool(first_seen and datetime.now(timezone.utc) - first_seen < NEW_WINDOW)
    data["first_seen"] = row["first_seen"]
    data["is_new"] = is_new
    return data


def _stable(payload_json: str) -> str:
    """The listing with per-fetch noise removed, for change detection.

    `citations[].retrieval_timestamp` is stamped on every crawl, so comparing raw
    JSON marks every re-fetch as an edit — which burns revisions and pushes empty
    deltas to every client. Compare on the substantive fields only.
    """
    try:
        obj = json.loads(payload_json)
    except (TypeError, ValueError):
        return payload_json
    for cite in obj.get("citations") or []:
        cite.pop("retrieval_timestamp", None)
        cite.pop("publication_date", None)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def upsert_many(locations: Iterable[KoreanLocation]) -> Tuple[int, int]:
    """Insert new listings, refresh existing ones. Returns (added, updated).

    An existing row keeps its original first_seen, so re-crawling never resets
    the "new" badge, and a row that had been delisted is revived cleanly.
    """
    added = updated = 0
    now = _now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            for loc in locations:
                payload = json.dumps(loc.model_dump(), ensure_ascii=False)
                existing = c.execute(
                    "SELECT data, delisted_at FROM locations WHERE id=?", (loc.id,)
                ).fetchone()
                if existing is None:
                    c.execute(
                        """INSERT INTO locations
                           (id,data,first_seen,last_seen,delisted_at,miss_count,rev,
                            region,category,price,window_dir,parking,
                            provider,listing_kind,canonical_id,lat,lon)
                           VALUES (?,?,?,?,NULL,0,?,?,?,?,?,?,?,?,?,?,?)""",
                        (loc.id, payload, now, now, _next_rev(c),
                         loc.region_category, loc.category, loc.price_per_hour,
                         loc.specs.window_direction, loc.specs.parking_spots,
                         loc.provider, loc.listing_kind, loc.canonical_id,
                         loc.latitude, loc.longitude),
                    )
                    added += 1
                else:
                    unchanged = (
                        existing["delisted_at"] is None
                        and _stable(existing["data"]) == _stable(payload)
                    )
                    if unchanged:
                        # Still there, nothing to tell clients — just refresh liveness.
                        c.execute(
                            "UPDATE locations SET last_seen=?, miss_count=0 WHERE id=?",
                            (now, loc.id),
                        )
                        continue
                    c.execute(
                        """UPDATE locations
                           SET data=?, last_seen=?, delisted_at=NULL, miss_count=0, rev=?,
                               region=?, category=?, price=?, window_dir=?, parking=?,
                               provider=?, listing_kind=?, canonical_id=?, lat=?, lon=?
                           WHERE id=?""",
                        (payload, now, _next_rev(c),
                         loc.region_category, loc.category, loc.price_per_hour,
                         loc.specs.window_direction, loc.specs.parking_spots,
                         loc.provider, loc.listing_kind, loc.canonical_id,
                         loc.latitude, loc.longitude, loc.id),
                    )
                    updated += 1
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return added, updated


def mark_absent(seen_ids: set, candidate_ids: set) -> int:
    """Record a miss for candidates the crawl covered but did not find.

    Only ids the crawl actually looked for are considered, so a partial crawl
    can never delist the rest of the catalogue. Delisting needs MISS_THRESHOLD
    consecutive misses.
    """
    missing = candidate_ids - seen_ids
    if not missing:
        return 0
    delisted = 0
    now = _now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            for lid in missing:
                row = c.execute(
                    "SELECT miss_count, delisted_at FROM locations WHERE id=?", (lid,)
                ).fetchone()
                if row is None or row["delisted_at"]:
                    continue
                misses = row["miss_count"] + 1
                if misses >= MISS_THRESHOLD:
                    c.execute(
                        "UPDATE locations SET miss_count=?, delisted_at=?, rev=? WHERE id=?",
                        (misses, now, _next_rev(c), lid),
                    )
                    delisted += 1
                else:
                    c.execute("UPDATE locations SET miss_count=? WHERE id=?", (misses, lid))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return delisted


def prune_tombstones() -> int:
    """Reclaim rows that have been delisted long enough that no client needs them."""
    cutoff = (datetime.now(timezone.utc) - TOMBSTONE_RETENTION).isoformat()
    with connect() as c:
        cur = c.execute(
            "DELETE FROM locations WHERE delisted_at IS NOT NULL AND delisted_at < ?", (cutoff,)
        )
        return cur.rowcount or 0


# ── Reads ───────────────────────────────────────────────────────────────────
def all_ids(include_delisted: bool = False) -> set:
    q = "SELECT id FROM locations" + ("" if include_delisted else " WHERE delisted_at IS NULL")
    with connect() as c:
        return {r["id"] for r in c.execute(q)}


def count(include_delisted: bool = False) -> int:
    q = "SELECT COUNT(*) n FROM locations" + ("" if include_delisted else " WHERE delisted_at IS NULL")
    with connect() as c:
        return c.execute(q).fetchone()["n"]


def by_id(location_id: str) -> Optional[Dict[str, Any]]:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM locations WHERE id=? AND delisted_at IS NULL", (location_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def search(
    category: Optional[str] = None,
    region: Optional[str] = None,
    max_price: Optional[int] = None,
    window_dir: Optional[str] = None,
    min_parking: Optional[int] = None,
    provider: Optional[str] = None,
    listing_kind: Optional[str] = None,
    skip: int = 0,
    limit: int = 60,
) -> Tuple[List[Dict[str, Any]], int]:
    """Filtered page plus the total match count. Newest listings sort first so a
    fresh find is the first thing a scout sees."""
    where = ["delisted_at IS NULL"]
    args: List[Any] = []

    if category and category != "전체":
        where.append("category = ?")
        args.append(category)
    if region and region != "전체":
        where.append("region = ?")
        args.append(region)
    if max_price:
        # A listing whose price could not be read is excluded rather than shown as free.
        where.append("price > 0 AND price <= ?")
        args.append(max_price)
    if window_dir and window_dir != "전체":
        where.append("window_dir LIKE ?")
        args.append(f"%{window_dir}%")
    if min_parking:
        where.append("parking >= ?")
        args.append(min_parking)
    if provider and provider != "전체":
        where.append("provider = ?")
        args.append(provider)
    if listing_kind and listing_kind != "전체":
        # "bookable" is the default view: a public-record location is a real
        # place but not something a scout can reserve, and mixing the two would
        # imply availability nobody verified.
        where.append("listing_kind = ?")
        args.append(listing_kind)

    clause = " AND ".join(where)
    with connect() as c:
        total = c.execute(f"SELECT COUNT(*) n FROM locations WHERE {clause}", args).fetchone()["n"]
        rows = c.execute(
            f"SELECT * FROM locations WHERE {clause} ORDER BY first_seen DESC, id DESC LIMIT ? OFFSET ?",
            [*args, limit, skip],
        ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def changes_since(since_rev: int, limit: int = 500) -> Dict[str, Any]:
    """The delta a client needs to catch up from `since_rev`.

    `truncated` tells the client to call again rather than assume it is current —
    without it a client would jump its cursor past changes it never received.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM locations WHERE rev > ? ORDER BY rev ASC LIMIT ?",
            (since_rev, limit + 1),
        ).fetchall()

        truncated = len(rows) > limit
        rows = rows[:limit]

        upserted = [_row_to_dict(r) for r in rows if r["delisted_at"] is None]
        removed = [r["id"] for r in rows if r["delisted_at"] is not None]
        # Only advance as far as we actually served.
        next_rev = rows[-1]["rev"] if rows else since_rev
        total = c.execute("SELECT COUNT(*) n FROM locations WHERE delisted_at IS NULL").fetchone()["n"]

    return {
        "version": next_rev,
        "truncated": truncated,
        "upserted": upserted,
        "removed": removed,
        "catalog_size": total,
        "new_count": new_count(),
    }


def new_count() -> int:
    cutoff = (datetime.now(timezone.utc) - NEW_WINDOW).isoformat()
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) n FROM locations WHERE delisted_at IS NULL AND first_seen > ?",
            (cutoff,),
        ).fetchone()["n"]


def stats() -> Dict[str, Any]:
    with connect() as c:
        live = c.execute("SELECT COUNT(*) n FROM locations WHERE delisted_at IS NULL").fetchone()["n"]
        gone = c.execute("SELECT COUNT(*) n FROM locations WHERE delisted_at IS NOT NULL").fetchone()["n"]
    return {
        "live": live,
        "delisted": gone,
        "new_within_72h": new_count(),
        "version": current_rev(),
        "last_crawl": get_meta("last_crawl"),
        "crawl_status": get_meta("crawl_status"),
        "db_path": str(DB_PATH),
    }


# ── Per-provider crawl state ────────────────────────────────────────────────
def is_ephemeral() -> bool:
    """True when this process's database cannot outlive it.

    Cloud Run sets K_SERVICE and gives each instance a private in-memory
    filesystem, so a write here is lost the moment the instance recycles — and
    two instances would diverge. The catalogue ships inside the image instead,
    and anything that writes has to know that rather than pretending to work.
    """
    return bool(os.getenv("K_SERVICE"))


def snapshot_taken_at() -> Optional[str]:
    """When the baked catalogue was last written, for the UI to state plainly."""
    with connect() as c:
        row = c.execute("SELECT MAX(last_seen) AS t FROM locations").fetchone()
        return row["t"] if row and row["t"] else None


def provider_state(provider: str) -> Dict[str, Any]:
    with connect() as c:
        row = c.execute("SELECT * FROM provider_state WHERE provider=?", (provider,)).fetchone()
        return dict(row) if row else {}


def set_provider_state(
    provider: str,
    *,
    status: Optional[str] = None,
    last_error: Optional[str] = None,
    touch_crawl: bool = False,
    rights_status: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> None:
    """Upsert one provider's row. Only the fields passed are written, so a
    status update does not clobber the rights basis recorded at registration."""
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO provider_state(provider) VALUES (?)", (provider,)
        )
        sets, args = [], []
        if status is not None:
            sets.append("status=?"); args.append(status)
        if last_error is not None:
            sets.append("last_error=?"); args.append(last_error)
        if touch_crawl:
            sets.append("last_crawl=?"); args.append(_now())
        if rights_status is not None:
            sets.append("rights_status=?"); args.append(rights_status)
        if enabled is not None:
            sets.append("enabled=?"); args.append(1 if enabled else 0)
        # Count is derived, never passed in: it must reflect the table, not what
        # a caller believes it wrote.
        sets.append("listings=(SELECT COUNT(*) FROM locations WHERE provider=? AND delisted_at IS NULL)")
        args.append(provider)
        if sets:
            args.append(provider)
            c.execute(f"UPDATE provider_state SET {', '.join(sets)} WHERE provider=?", args)


def provider_breakdown() -> List[Dict[str, Any]]:
    """Live counts per provider and listing kind, joined with crawl state."""
    with connect() as c:
        rows = c.execute(
            """SELECT l.provider,
                      l.listing_kind,
                      COUNT(*) AS n
               FROM locations l
               WHERE l.delisted_at IS NULL
               GROUP BY l.provider, l.listing_kind
               ORDER BY n DESC"""
        ).fetchall()
        state = {r["provider"]: dict(r) for r in c.execute("SELECT * FROM provider_state")}
    out: List[Dict[str, Any]] = []
    for r in rows:
        st = state.get(r["provider"], {})
        out.append({
            "provider": r["provider"],
            "listing_kind": r["listing_kind"],
            "count": r["n"],
            "status": st.get("status", "unknown"),
            "last_crawl": st.get("last_crawl"),
            "rights_status": st.get("rights_status", "robots_allowed"),
            "enabled": bool(st.get("enabled", 1)),
        })
    return out


def ids_for_provider(provider: str) -> set:
    """Every live id this provider owns. Delisting is scoped by provider so a
    failing source can never mark another source's listings as gone."""
    with connect() as c:
        return {
            r["id"]
            for r in c.execute(
                "SELECT id FROM locations WHERE provider=? AND delisted_at IS NULL",
                (provider,),
            )
        }
