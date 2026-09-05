"""Store behaviour: delta sync, delisting, and the 72h freshness window."""
from datetime import datetime, timedelta, timezone

import pytest

from app import store
from app.agent.tools.hourplace_ingest import build_location


def _meta(pid=1, price=50000, title="테스트 스튜디오"):
    return {
        "id": pid, "title": title, "description": "82.6㎡ 규모의 촬영 공간입니다.",
        "region": "서울", "locality": "성동구", "lat": 37.5, "lng": 127.0,
        "floor": "1", "main_image_path": "p/a.jpg", "category_main": "자연광스튜디오",
        "address": "서울 성동구", "og_title": title,
        "price_low": price, "price_high": price, "rating_value": 5, "rating_count": 1,
    }


def _gallery():
    return {"images": ["https://img.hourplace.co.kr/p/1.jpg"], "title": "t", "captions": []}


def _loc(pid=1, price=50000, title="테스트 스튜디오"):
    return build_location(pid, _meta(pid, price, title), _gallery())


@pytest.fixture(autouse=True)
def _clean():
    with store.connect() as c:
        c.execute("DELETE FROM locations")
        c.execute("DELETE FROM provider_state")
        c.execute("UPDATE meta SET value='0' WHERE key='rev'")
    yield


def test_delta_sync_returns_only_what_changed():
    store.upsert_many([_loc(1), _loc(2)])
    after_first = store.current_rev()

    store.upsert_many([_loc(3)])
    delta = store.changes_since(after_first)

    assert [l["id"] for l in delta["upserted"]] == ["hp_3"]
    assert delta["removed"] == []
    assert delta["version"] == store.current_rev()


def test_reingesting_identical_data_produces_no_delta():
    """A crawl pass that finds nothing new must not push empty updates to clients."""
    store.upsert_many([_loc(1)])
    rev = store.current_rev()
    store.upsert_many([_loc(1)])
    assert store.current_rev() == rev
    assert store.changes_since(rev)["upserted"] == []


def test_price_change_is_delivered_as_an_update():
    store.upsert_many([_loc(1, price=50000)])
    rev = store.current_rev()
    store.upsert_many([_loc(1, price=77000)])

    delta = store.changes_since(rev)
    assert len(delta["upserted"]) == 1
    assert delta["upserted"][0]["price_per_hour"] == 77000


def test_delisting_needs_repeated_misses_then_surfaces_as_removed():
    store.upsert_many([_loc(1), _loc(2)])
    rev = store.current_rev()
    candidates = {"hp_1", "hp_2"}

    # One missed pass is treated as a transient failure, not a removal.
    assert store.mark_absent({"hp_1"}, candidates) == 0
    assert store.changes_since(rev)["removed"] == []

    # A second consecutive miss delists it.
    assert store.mark_absent({"hp_1"}, candidates) == 1
    delta = store.changes_since(rev)
    assert delta["removed"] == ["hp_2"]
    assert store.count() == 1


def test_a_partial_crawl_cannot_delist_listings_it_never_checked():
    store.upsert_many([_loc(1), _loc(2), _loc(3)])
    # The pass only looked at hp_1 and found it; the others were never candidates.
    for _ in range(5):
        store.mark_absent({"hp_1"}, {"hp_1"})
    assert store.count() == 3


def test_relisting_revives_a_delisted_row_without_resetting_first_seen():
    store.upsert_many([_loc(1)])
    original_first_seen = store.by_id("hp_1")["first_seen"]
    store.mark_absent(set(), {"hp_1"})
    store.mark_absent(set(), {"hp_1"})
    assert store.by_id("hp_1") is None

    store.upsert_many([_loc(1)])
    revived = store.by_id("hp_1")
    assert revived is not None
    assert revived["first_seen"] == original_first_seen


def test_new_badge_expires_after_72_hours():
    store.upsert_many([_loc(1)])
    assert store.by_id("hp_1")["is_new"] is True
    assert store.new_count() == 1

    stale = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    with store.connect() as c:
        c.execute("UPDATE locations SET first_seen=? WHERE id='hp_1'", (stale,))

    assert store.by_id("hp_1")["is_new"] is False
    assert store.new_count() == 0


def test_sync_truncation_does_not_skip_changes():
    """A client must never advance its cursor past rows it was not sent."""
    store.upsert_many([_loc(i) for i in range(1, 11)])
    delta = store.changes_since(0, limit=4)
    assert delta["truncated"] is True
    assert len(delta["upserted"]) == 4
    # Continuing from the served version picks up exactly where it left off.
    nxt = store.changes_since(delta["version"], limit=100)
    assert len(nxt["upserted"]) == 6


def test_tombstones_are_pruned_after_retention():
    store.upsert_many([_loc(1)])
    store.mark_absent(set(), {"hp_1"})
    store.mark_absent(set(), {"hp_1"})
    old = (datetime.now(timezone.utc) - store.TOMBSTONE_RETENTION - timedelta(days=1)).isoformat()
    with store.connect() as c:
        c.execute("UPDATE locations SET delisted_at=? WHERE id='hp_1'", (old,))
    assert store.prune_tombstones() == 1
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM locations").fetchone()["n"] == 0


def test_newest_listings_sort_first():
    store.upsert_many([_loc(1, title="오래된 곳")])
    older = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    with store.connect() as c:
        c.execute("UPDATE locations SET first_seen=? WHERE id='hp_1'", (older,))
    store.upsert_many([_loc(2, title="새로 뜬 곳")])

    rows, _ = store.search()
    assert rows[0]["id"] == "hp_2"


# ── Multi-source invariants ────────────────────────────────────────────────
def _prov_loc(lid, provider="hourplace", kind="bookable", **kw):
    from app.models.korean_locations import KoreanLocation, LocationSpec

    base = dict(
        id=lid, name=f"장소 {lid}", tagline="", region="서울 성동", region_category="서울",
        category="모던 스튜디오", price_per_hour=50000, price_per_day=400000, min_hours=2,
        rating=4.5, review_count=3, images=["https://example.test/a.jpg"],
        specs=LocationSpec(area_sqm=100, area_pyeong=30, ceiling_height_m=3.0,
                           window_direction="남향", natural_light_type="자연광",
                           golden_hour_window="17:00", power_capacity="20kW",
                           parking_spots=2, has_freight_elevator=False,
                           sound_recording_quality="보통"),
        tags=[], permit_summary="", citations=[],
        provider=provider, listing_kind=kind,
    )
    base.update(kw)
    return KoreanLocation(**base)


def test_delisting_is_scoped_to_one_provider():
    """A source that fails or is switched off must never mark another source's
    listings as gone — the whole reason crawl state is per provider."""
    from app import store

    store.upsert_many([_prov_loc("hp_1"), _prov_loc("hp_2"), _prov_loc("pd_1", provider="public_data",
                                                          kind="reference")])
    assert store.ids_for_provider("hourplace") == {"hp_1", "hp_2"}
    assert store.ids_for_provider("public_data") == {"pd_1"}

    # A hourplace pass that saw neither of its ids, twice, delists only its own.
    for _ in range(2):
        store.mark_absent(set(), store.ids_for_provider("hourplace"))
    assert store.by_id("pd_1") is not None
    assert store.by_id("hp_1") is None


def test_reference_rows_are_excluded_from_the_default_view():
    """A public-record location is a real place but not a rentable listing;
    the default catalogue view must not imply it can be booked."""
    from app import store

    store.upsert_many([_prov_loc("hp_1"), _prov_loc("pd_1", provider="public_data", kind="reference")])
    _, bookable = store.search(listing_kind="bookable")
    _, everything = store.search(listing_kind="전체")
    assert bookable == 1
    assert everything == 2


def test_provider_breakdown_counts_by_source_and_kind():
    from app import store

    store.upsert_many([_prov_loc("hp_1"), _prov_loc("hp_2"),
                       _prov_loc("pd_1", provider="public_data", kind="reference")])
    store.set_provider_state("hourplace", status="idle", rights_status="robots_allowed")
    rows = {(r["provider"], r["listing_kind"]): r for r in store.provider_breakdown()}
    assert rows[("hourplace", "bookable")]["count"] == 2
    assert rows[("public_data", "reference")]["count"] == 1
    assert rows[("hourplace", "bookable")]["rights_status"] == "robots_allowed"


def test_provenance_survives_a_round_trip():
    from app import store

    store.upsert_many([_prov_loc("pd_9", provider="public_data", kind="reference",
                            latitude=37.5, longitude=127.0,
                            rights_status="public_open_data")])
    row = store.by_id("pd_9")
    assert row["provider"] == "public_data"
    assert row["listing_kind"] == "reference"
    assert row["rights_status"] == "public_open_data"
    assert row["latitude"] == 37.5
