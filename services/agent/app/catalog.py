"""
Read-side view of the listing store, as KoreanLocation objects.

The store speaks dicts (it round-trips JSON through SQLite); the script matcher
and chat assistant want typed models. This is the seam between the two, so those
callers never learn where the data physically lives.

Everything here is real, currently-listed inventory. There is deliberately no
demo, sample or synthetic data anywhere in this module: an empty catalog is
honest, an invented one is not.
"""
from typing import List, Optional

from app import store
from app.models.korean_locations import KoreanLocation

# Reading every listing to rank two scenes is wasteful once the catalog is large,
# so the matcher works from a bounded working set rather than the whole table.
WORKING_SET = 1500


def _to_model(data: dict) -> Optional[KoreanLocation]:
    try:
        return KoreanLocation(**data)
    except Exception:
        return None


def all_locations(limit: int = WORKING_SET) -> List[KoreanLocation]:
    rows, _ = store.search(limit=limit)
    return [m for m in (_to_model(r) for r in rows) if m]


def size() -> int:
    return store.count()


def by_id(location_id: str) -> Optional[KoreanLocation]:
    row = store.by_id(location_id)
    return _to_model(row) if row else None


def search(
    category: Optional[str] = None,
    region: Optional[str] = None,
    max_price: Optional[int] = None,
    window_dir: Optional[str] = None,
    min_parking: Optional[int] = None,
    limit: int = WORKING_SET,
) -> List[KoreanLocation]:
    rows, _ = store.search(
        category=category,
        region=region,
        max_price=max_price,
        window_dir=window_dir,
        min_parking=min_parking,
        limit=limit,
    )
    return [m for m in (_to_model(r) for r in rows) if m]


# ── Test seam ───────────────────────────────────────────────────────────────
def replace_all(locations: List[KoreanLocation]) -> None:
    """Reset the store to exactly these listings. Used by tests; production
    writes go through the crawler and the ingest endpoint."""
    with store.connect() as c:
        c.execute("DELETE FROM locations")
        c.execute("UPDATE meta SET value='0' WHERE key='rev'")
    if locations:
        store.upsert_many(locations)


def merge(locations: List[KoreanLocation]) -> int:
    added, _ = store.upsert_many(locations)
    return added
