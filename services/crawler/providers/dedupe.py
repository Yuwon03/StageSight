"""
Same venue, several platforms.

A studio listed on both hourplace and spacecloud is one place with two booking
links, not two search results. Merging is done by assigning both rows the same
`canonical_id`; neither row is deleted, because each carries its own price and
its own URL and the user needs both.

Matching is deliberately conservative. A false merge hides a real listing behind
another one's price, which is worse than showing a duplicate — so a pair merges
only on strong evidence:

  * an identical phone number, or
  * a similar name AND (the same address OR coordinates within ~120 m), or
  * the same address AND coordinates within ~60 m, even with dissimilar names.

That last clause exists because platforms transliterate: "REAL HOUSE" on one
site is "리얼하우스" on another, and a bigram comparison scores those at zero.
Address plus tight coordinates is strong enough on its own — except in a
building holding several rentable units, so a name carrying a floor or unit
marker ("3층", "B관", "A홀") is excluded from that clause and must match by name
like everything else.

Name similarity alone never merges: "스튜디오 A" and "스튜디오 B" at one address
are different rentable spaces.
"""
from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Platform decorations that carry no identity — stripped before comparing.
_NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|"
    r"(스튜디오|studio|촬영|대관|공간|space|렌탈|rental|호리존|hall|홀)",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def norm_name(name: str) -> str:
    return _NON_WORD.sub("", _NOISE.sub("", name or "").lower())


def norm_address(addr: str) -> str:
    """Korean addresses vary in spacing and in 시/도 abbreviation."""
    a = (addr or "").strip()
    a = a.replace("서울특별시", "서울").replace("경기도", "경기").replace("인천광역시", "인천")
    a = a.replace("부산광역시", "부산").replace("대구광역시", "대구").replace("광주광역시", "광주")
    a = a.replace("대전광역시", "대전").replace("울산광역시", "울산").replace("제주특별자치도", "제주")
    return _NON_WORD.sub("", a.lower())


def norm_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _dist_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Equirectangular approximation — accurate enough at these distances and
    far cheaper than haversine over a 12k × 12k comparison."""
    lat1, lon1 = a
    lat2, lon2 = b
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return math.hypot(x, y) * 6_371_000


def name_similarity(a: str, b: str) -> float:
    """Character-bigram Dice coefficient. Robust to the word-order and spacing
    differences between platforms, and needs no dependency."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ga = {na[i : i + 2] for i in range(len(na) - 1)} or {na}
    gb = {nb[i : i + 2] for i in range(len(nb) - 1)} or {nb}
    return 2 * len(ga & gb) / (len(ga) + len(gb))


NEAR_METRES = 120.0
SAME_BUILDING_METRES = 60.0
NAME_THRESHOLD = 0.62

# A listing naming a floor or unit is one space inside a building, not the
# building — so it never merges on address alone.
_UNIT_MARKER = re.compile(r"(\d+\s*층|지하\s*\d*|[A-Za-z가-힣]\s?(관|홀|호|동)\b|#\s?\d+)")


def has_unit_marker(name: str) -> bool:
    return bool(_UNIT_MARKER.search(name or ""))


def is_same_venue(a: Dict, b: Dict) -> bool:
    """Strong evidence only — see the module docstring."""
    if a.get("provider") == b.get("provider"):
        return False  # two listings on one platform are two rentable spaces

    pa, pb = norm_phone(a.get("phone", "")), norm_phone(b.get("phone", ""))
    if pa and pa == pb:
        return True

    aa, ab = norm_address(a.get("address", "")), norm_address(b.get("address", ""))
    same_address = bool(aa) and aa == ab

    # Transliteration case: same address, essentially the same spot, and neither
    # side claims to be a specific unit within the building.
    if (
        same_address
        and _near(a, b, SAME_BUILDING_METRES)
        and not has_unit_marker(a.get("name", ""))
        and not has_unit_marker(b.get("name", ""))
    ):
        return True

    sim = name_similarity(a.get("name", ""), b.get("name", ""))
    if sim < NAME_THRESHOLD:
        return False

    if same_address:
        return True

    if _near(a, b, NEAR_METRES):
        return True

    return False


def _near(a: Dict, b: Dict, metres: float) -> bool:
    if any(a.get(k) is None for k in ("latitude", "longitude")):
        return False
    if any(b.get(k) is None for k in ("latitude", "longitude")):
        return False
    return _dist_m((a["latitude"], a["longitude"]), (b["latitude"], b["longitude"])) <= metres


def assign_canonical(rows: Sequence[Dict]) -> Dict[str, str]:
    """Group rows into venues; returns {row id: canonical id}.

    Union-find over candidate pairs. Candidates are bucketed by a coarse
    geohash-ish key so this stays near-linear instead of comparing every row
    with every other one — at 12k rows the quadratic version is 72M comparisons.
    """
    parent: Dict[str, str] = {r["id"]: r["id"] for r in rows}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            # Lowest id wins, so the canonical id is stable across runs.
            hi, lo = (rx, ry) if rx > ry else (ry, rx)
            parent[hi] = lo

    buckets: Dict[str, List[Dict]] = {}
    for r in rows:
        keys = set()
        if r.get("latitude") is not None and r.get("longitude") is not None:
            # ~1.1 km cells; a 120 m pair can straddle one, so neighbours too.
            la, lo = round(r["latitude"], 2), round(r["longitude"], 2)
            for dla in (-0.01, 0.0, 0.01):
                for dlo in (-0.01, 0.0, 0.01):
                    keys.add(f"g{la + dla:.2f},{lo + dlo:.2f}")
        addr = norm_address(r.get("address", ""))
        if addr:
            keys.add(f"a{addr[:12]}")
        nm = norm_name(r.get("name", ""))
        if nm:
            keys.add(f"n{nm[:4]}")
        for k in keys:
            buckets.setdefault(k, []).append(r)

    seen_pairs = set()
    for group in buckets.values():
        if len(group) < 2 or len(group) > 400:
            continue  # a bucket that large is a bad key, not a venue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                pair = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if is_same_venue(a, b):
                    union(a["id"], b["id"])

    # Only rows that actually merged get a canonical id; a lone listing does not
    # need one and setting it would churn a revision for every row in the table.
    roots: Dict[str, int] = {}
    for r in rows:
        roots[find(r["id"])] = roots.get(find(r["id"]), 0) + 1
    return {r["id"]: find(r["id"]) for r in rows if roots[find(r["id"])] > 1}
