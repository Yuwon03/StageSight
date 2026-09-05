"""
Simulator evaluation harness.

    .venv/bin/python -m evaluation.run_eval --round r1 --listings 10
    .venv/bin/python -m evaluation.run_eval --round r2 --cases orbit90,wide --listings 10
    .venv/bin/python -m evaluation.run_eval --report r1 r2

Each run: pick real listings spread across space types, render the case matrix
against the live API with the cache bypassed, then score every pair twice —
once with model-free metrics, once with a Gemini judge — and write one JSONL row
per case plus an aggregate summary.

Rounds are comparable because the listing sample is deterministic (sorted by id,
no randomness) and the judge runs at temperature 0. A prompt change is an
improvement only if the same cells score higher in the next round.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import metrics as M          # noqa: E402
from evaluation.judge import judge_pair, overall, SCORE_KEYS  # noqa: E402
from evaluation.metrics import homography_inlier_ratio, GLOBAL_WARP_RATIO  # noqa: E402

API = "http://localhost:8080"
PROMPT_VERSION_ARG = "v2"
IMAGE_MODEL_ARG = ""  # set by --image-model
RENDER_STRATEGY_ARG = "standard"
OUT_DIR = Path(__file__).resolve().parent / "results"
IMG_DIR = OUT_DIR / "images"

# Space types we want represented; the sample takes the first listings of each
# by id so the same set comes back on every round.
CATEGORIES = ["모던 스튜디오", "럭셔리 하우스", "카페/갤러리", "전통 한옥", "자연/야외", "빈티지/창고"]

# The matrix. Each case isolates one axis so a regression is attributable.
CASES: List[Dict] = [
    # identity / light
    dict(name="baseline",  rotation=0,  tilt=0,   zoom=10, time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="night",     rotation=0,  tilt=0,   zoom=10, time_label="22:00", light_phase="night",       sun_altitude_deg=-28),
    dict(name="golden",    rotation=0,  tilt=0,   zoom=10, time_label="18:30", light_phase="golden_hour", sun_altitude_deg=5),
    dict(name="blue",      rotation=0,  tilt=0,   zoom=10, time_label="19:30", light_phase="blue_hour",   sun_altitude_deg=-4),
    dict(name="morning",   rotation=0,  tilt=0,   zoom=10, time_label="08:00", light_phase="morning",     sun_altitude_deg=25),
    # lens
    dict(name="wide",      rotation=0,  tilt=0,   zoom=1,  time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="tele",      rotation=0,  tilt=0,   zoom=20, time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    # tilt
    dict(name="high",      rotation=0,  tilt=35,  zoom=8,  time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="bird",      rotation=0,  tilt=90,  zoom=6,  time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="low",       rotation=0,  tilt=-35, zoom=8,  time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="worm",      rotation=0,  tilt=-80, zoom=6,  time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    # orbit — the known weak axis
    dict(name="orbit45",   rotation=45, tilt=0,   zoom=10, time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
    dict(name="orbit90",   rotation=90, tilt=0,   zoom=10, time_label="14:00", light_phase="midday",      sun_altitude_deg=60),
]

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def zoom_to_focal(z: int) -> int:
    return int(round(16 * (85 / 16) ** ((max(1, min(20, z)) - 1) / 19)))


async def pick_listings(client: httpx.AsyncClient, per_category: int) -> List[Dict]:
    """Deterministic sample: for each category, the lowest-id listings that have
    a photo. Same set every round, so scores are comparable."""
    picked: List[Dict] = []
    for cat in CATEGORIES:
        r = await client.get(f"{API}/api/locations", params={"category": cat, "limit": 60})
        rows = [x for x in r.json() if x.get("images")]
        rows.sort(key=lambda x: x["id"])
        for row in rows[:per_category]:
            picked.append({
                "id": row["id"],
                "name": row["name"][:44],
                "category": row["category"],
                "image": row["images"][0],
                "window": row["specs"]["window_direction"],
            })
    return picked


async def fetch_source(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    try:
        r = await client.get(url, headers={"User-Agent": BROWSER_UA})
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


async def assert_server_prompt_is_current(client: httpx.AsyncClient) -> None:
    """Refuse to measure a prompt the server is not running.

    uvicorn is started without --reload, so editing frame_prompt.py changes
    nothing until it is restarted. A round run against a stale process produces
    real-looking numbers for the previous prompt, and they get written up as the
    new one's. Cheap to check, impossible to notice afterwards.
    """
    from app.agent.tools.frame_prompt import PROMPT_VERSION as LOCAL

    try:
        r = await client.get(f"{API}/api/simulate/prompt-version", timeout=10.0)
        served = r.json().get("prompt_version", "") if r.status_code == 200 else ""
    except Exception as e:
        served = f"<unreachable: {e}>"
    if served != LOCAL:
        sys.exit(
            f"ABORT: the server is serving prompt {served!r} but this checkout is {LOCAL!r}.\n"
            f"       uvicorn does not reload — restart it before running a round, or the\n"
            f"       numbers will describe the previous prompt."
        )
    print(f"server prompt: {served}")


# Gemini image generation is rate limited per minute. r5 lost 50 of 72 rows to a
# solid block of 429s because a failed render was silently dropped — and because
# the harness walks listings in order, what survived was two categories out of
# six, i.e. exactly the biased partial sample that has misread three rounds
# already. A dropped row is worse than a slow one: retry, and say why on the way.
RENDER_ATTEMPTS = 5


async def render(client: httpx.AsyncClient, listing: Dict, case: Dict):
    body = {
        "image_url": listing["image"],
        "date_label": "2026-09-01",
        "window_direction": listing["window"],
        "space_category": listing["category"],
        "bypass_cache": True,
        "prompt_version": PROMPT_VERSION_ARG,
        "image_model": IMAGE_MODEL_ARG,
        "render_strategy": RENDER_STRATEGY_ARG,
        **{k: v for k, v in case.items() if k != "name"},
    }
    why = "unknown"
    started = time.perf_counter()
    for attempt in range(RENDER_ATTEMPTS):
        try:
            r = await client.post(f"{API}/api/simulate/frame", json=body, timeout=300.0)
            if r.status_code == 200:
                payload = r.json()
                return (
                    base64.b64decode(payload["image_data_url"].split(",", 1)[1]),
                    {
                        "model": payload.get("model", ""),
                        "prompt_version": payload.get("prompt_version", ""),
                        "prompt_fingerprint": payload.get("prompt_fingerprint", ""),
                        "render_strategy": payload.get("render_strategy", RENDER_STRATEGY_ARG),
                        "image_tier": payload.get("image_tier", ""),
                        "latency_s": round(time.perf_counter() - started, 3),
                        "attempts": attempt + 1,
                        "note": payload.get("note", ""),
                    },
                )
            why = f"HTTP {r.status_code} {r.text[:120]}"
            # 503 means the key is missing; no amount of waiting fixes that.
            if r.status_code == 503:
                break
        except Exception as e:
            why = f"{type(e).__name__}: {e}"
        if attempt < RENDER_ATTEMPTS - 1:
            # 20s, 40s, 80s, 160s — long enough to clear a per-minute quota.
            await asyncio.sleep(20 * (2 ** attempt))
    print(f"      render gave up on {listing['id']} {case['name']}: {why}", flush=True)
    return None


async def run_round(round_id: str, per_category: int, only_cases: Optional[List[str]], concurrency: int):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (IMG_DIR / round_id).mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{round_id}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.open():
            try:
                d = json.loads(line)
                done.add((d["listing_id"], d["case"]))
            except Exception:
                pass
        print(f"resuming {round_id}: {len(done)} rows already recorded")

    cases = [c for c in CASES if not only_cases or c["name"] in only_cases]

    async with httpx.AsyncClient(timeout=httpx.Timeout(320.0, connect=10.0), follow_redirects=True) as client:
        await assert_server_prompt_is_current(client)
        listings = await pick_listings(client, per_category)
        print(f"{len(listings)} listings × {len(cases)} cases = {len(listings)*len(cases)} generations")

        sources: Dict[str, bytes] = {}
        for l in listings:
            b = await fetch_source(client, l["image"])
            if b:
                sources[l["id"]] = b
        print(f"source photos fetched: {len(sources)}/{len(listings)}")

        sem = asyncio.Semaphore(concurrency)
        counter = {"n": 0, "total": len(listings) * len(cases)}
        fh = out_path.open("a", encoding="utf-8")
        lock = asyncio.Lock()

        async def one(listing: Dict, case: Dict):
            key = (listing["id"], case["name"])
            if key in done or listing["id"] not in sources:
                return
            async with sem:
                rendered = await render(client, listing, case)
                counter["n"] += 1
                if rendered is None:
                    print(f"  [{counter['n']}/{counter['total']}] {listing['id']} {case['name']}: RENDER FAILED")
                    return
                out, generation = rendered
                img_path = IMG_DIR / round_id / f"{listing['id']}_{case['name']}.png"
                img_path.write_bytes(out)

                src = sources[listing["id"]]
                m = M.compare(src, out)
                inlier = await asyncio.to_thread(homography_inlier_ratio, src, out)
                enriched = {**case, "focal_mm": zoom_to_focal(case["zoom"]),
                            "space_category": listing["category"], "date_label": "2026-09-01"}
                flags = M.expectation_flags(m, enriched)
                # The judge is a network call; keep it off the render semaphore.
                scores = await asyncio.to_thread(judge_pair, src, out, enriched)

                row = {
                    "round": round_id,
                    "listing_id": listing["id"],
                    "listing_name": listing["name"],
                    "category": listing["category"],
                    "case": case["name"],
                    "metrics": {**m.as_dict(), "homography_inlier": inlier,
                                "explained_by_global_warp": (inlier is not None and inlier > GLOBAL_WARP_RATIO)},
                    "flags": flags,
                    "judge": scores,
                    "overall": overall(scores) if scores else None,
                    "generation": generation,
                    "image": str(img_path),
                }
                async with lock:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                o = row["overall"]
                print(f"  [{counter['n']}/{counter['total']}] {listing['id']:11} {case['name']:9} "
                      f"score={o if o is not None else '-'} phash={m.phash_distance}")

        await asyncio.gather(*[one(l, c) for l in listings for c in cases])
        fh.close()

    print(f"\nwrote {out_path}")
    report([round_id])


def load_rows(round_id: str) -> List[Dict]:
    p = OUT_DIR / f"{round_id}.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.open():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _warn_if_unbalanced(scored: List[Dict]) -> None:
    """A half-finished round is a biased sample, not a small random one: the
    harness walks listings in a fixed order, so an interrupted round is missing
    whole categories rather than a scattering of rows. Three rounds have already
    been misread this way (r2 showed 0% fabricated geometry at 54/156 against a
    true 10.3%; r4 showed light 4.00 at 16/72 against a true 3.51). Say so loudly
    rather than printing a mean that looks like a result."""
    per_case = Counter(r["case"] for r in scored)
    per_cat = Counter(r.get("category", "?") for r in scored)
    if not per_case:
        return
    lo, hi = min(per_case.values()), max(per_case.values())
    complaints = []
    if hi - lo > 1:
        thin = ", ".join(f"{c}={n}" for c, n in sorted(per_case.items(), key=lambda kv: kv[1]))
        complaints.append(f"cases are uneven ({thin})")
    if len(per_cat) < 6:
        complaints.append(f"only {len(per_cat)} of 6 space categories present: {', '.join(sorted(per_cat))}")
    if complaints:
        print("  !! INCOMPLETE — DO NOT READ THESE MEANS AS THE ROUND'S RESULT")
        for c in complaints:
            print(f"     - {c}")
        print("     Re-run the round to fill the missing rows before comparing.")


def report(round_ids: List[str]) -> None:
    print("\n" + "=" * 100)
    all_rows = {r: load_rows(r) for r in round_ids}

    for rid, rows in all_rows.items():
        scored = [r for r in rows if r.get("judge")]
        if not scored:
            print(f"{rid}: no scored rows"); continue
        print(f"\nROUND {rid} — {len(rows)} runs, {len(scored)} judged")
        _warn_if_unbalanced(scored)
        for k in SCORE_KEYS:
            vals = [r["judge"][k] for r in scored]
            print(f"  {k:9} mean {st.mean(vals):.2f}   (1-2: {sum(1 for v in vals if v<=2)}, 5: {sum(1 for v in vals if v==5)})")
        print(f"  {'OVERALL':9} mean {st.mean([r['overall'] for r in scored]):.3f}")
        print(f"  returned_input_unchanged: {sum(1 for r in scored if r['judge'].get('returned_input_unchanged'))}")
        print(f"  invented_duplicate_geom : {sum(1 for r in scored if r['judge'].get('invented_duplicate_geometry'))}")

        print("\n  BY CASE (overall / identity / light / camera / framing):")
        by_case = defaultdict(list)
        for r in scored:
            by_case[r["case"]].append(r)
        for case in [c["name"] for c in CASES]:
            rs = by_case.get(case) or []
            if not rs:
                continue
            print(f"    {case:9} n={len(rs):2}  {st.mean([r['overall'] for r in rs]):.2f}  "
                  + "  ".join(f"{k[:3]}={st.mean([r['judge'][k] for r in rs]):.1f}" for k in SCORE_KEYS))

        print("\n  BY SPACE TYPE:")
        by_cat = defaultdict(list)
        for r in scored:
            by_cat[r["category"]].append(r)
        for cat, rs in sorted(by_cat.items(), key=lambda kv: st.mean([r["overall"] for r in kv[1]])):
            print(f"    {cat:12} n={len(rs):2}  {st.mean([r['overall'] for r in rs]):.2f}")

        print("\n  ORBIT COMPLIANCE (model-free): share of renders that are just a global warp of the source")
        for case in ("baseline", "orbit45", "orbit90", "high", "bird"):
            rs = [r for r in scored if r["case"] == case and r["metrics"].get("homography_inlier") is not None]
            if not rs:
                continue
            warp = sum(1 for r in rs if r["metrics"]["explained_by_global_warp"])
            med = st.median([r["metrics"]["homography_inlier"] for r in rs])
            print(f"    {case:9} n={len(rs):2}  median inlier={med:.2f}  static-or-pan={warp}/{len(rs)}")

        # Where the metrics contradict the judge — the most informative rows.
        contradictions = [
            r for r in scored
            if r["flags"].get("produced_a_change") is False and r["judge"]["camera"] >= 4
        ]
        if contradictions:
            print(f"\n  ⚠ judge said the camera moved but pHash says the image is unchanged: {len(contradictions)}")
            for r in contradictions[:5]:
                print(f"      {r['listing_id']} {r['case']}: phash={r['metrics']['phash_distance']} camera={r['judge']['camera']}")

        print("\n  WORST 8 RUNS:")
        for r in sorted(scored, key=lambda r: r["overall"])[:8]:
            print(f"    {r['overall']:.2f} {r['listing_id']:11} {r['case']:9} {r['category'][:8]:8} "
                  f"— {r['judge']['worst_problem'][:70]}")

    if len(round_ids) > 1:
        print("\n" + "=" * 100)
        print("ROUND-OVER-ROUND (overall mean by case)")
        cases = [c["name"] for c in CASES]
        head = "  " + "case".ljust(10) + "".join(r.rjust(9) for r in round_ids) + "     delta"
        print(head)
        for case in cases:
            means = []
            for rid in round_ids:
                rs = [r for r in all_rows[rid] if r["case"] == case and r.get("judge")]
                means.append(st.mean([r["overall"] for r in rs]) if rs else None)
            if not any(m is not None for m in means):
                continue
            cells = "".join(("   —   " if m is None else f"{m:9.2f}") for m in means)
            delta = ""
            if means[0] is not None and means[-1] is not None:
                d = means[-1] - means[0]
                delta = f"  {d:+.2f}" + ("  ▲" if d > 0.08 else ("  ▼" if d < -0.08 else ""))
            print("  " + case.ljust(10) + cells + delta)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", help="round id, e.g. r1")
    ap.add_argument("--listings", type=int, default=2, help="listings per category")
    ap.add_argument("--cases", help="comma-separated case names to run")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--image-model", default="", help="override the image model for this round")
    ap.add_argument(
        "--render-strategy",
        default="standard",
        choices=["standard", "physical", "planned", "iterative"],
        help="fixed-camera relight workflow to evaluate",
    )
    ap.add_argument("--prompt-version", default="v2", choices=["v1", "v2"])
    ap.add_argument("--report", nargs="*", help="report on these round ids and exit")
    a = ap.parse_args()

    if a.report:
        report(a.report)
        return
    if not a.round:
        ap.error("--round is required unless --report is given")

    global PROMPT_VERSION_ARG, IMAGE_MODEL_ARG, RENDER_STRATEGY_ARG
    PROMPT_VERSION_ARG = a.prompt_version
    IMAGE_MODEL_ARG = a.image_model
    RENDER_STRATEGY_ARG = a.render_strategy
    print(f"prompt version: {PROMPT_VERSION_ARG}")
    print(f"image model: {IMAGE_MODEL_ARG or '<tier default>'}")
    print(f"render strategy: {RENDER_STRATEGY_ARG}")

    t0 = time.time()
    asyncio.run(run_round(a.round, a.listings, a.cases.split(",") if a.cases else None, a.concurrency))
    print(f"elapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
