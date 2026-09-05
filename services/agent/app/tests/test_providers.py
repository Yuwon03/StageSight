"""Multi-source catalogue: the invariants that keep sources from harming each other.

These are cheap unit tests, but each one guards a failure that is expensive or
embarrassing in production — republishing content without a licence, one source
delisting another's listings, or a public-record location being presented as a
bookable venue.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "crawler"))

from providers.base import Kind, Rights, stamp  # noqa: E402
from providers.dedupe import assign_canonical, is_same_venue, name_similarity  # noqa: E402
from providers.public_data import PublicDataProvider, _region_of  # noqa: E402
from providers.registry import PENDING, PROVIDERS, enabled_providers, roadmap  # noqa: E402


# ── Rights gating ───────────────────────────────────────────────────────────
def test_a_source_awaiting_permission_never_runs():
    """Adapters are written before permission arrives; the gate is what keeps
    one from being switched on by accident."""
    names = {p.name for p in enabled_providers()}
    for blocked in PENDING:
        assert blocked not in names


def test_naming_a_blocked_source_explicitly_still_refuses_it():
    """`--providers spacecloud` must not be a way around the gate."""
    assert enabled_providers(["spacecloud"]) == []


def test_every_live_provider_declares_a_rights_basis():
    for p in PROVIDERS.values():
        assert p.rights in {
            Rights.PUBLIC_OPEN_DATA,
            Rights.PARTNER_APPROVED,
            Rights.ROBOTS_ALLOWED,
        }


def test_roadmap_names_who_can_unblock_each_pending_source():
    blocked = [r for r in roadmap() if not r["enabled"]]
    assert blocked, "the roadmap should list the sources still being negotiated"
    for r in blocked:
        assert r["blocked_on"], f"{r['provider']} has no contact to chase"


# ── Availability is declared, never implied ─────────────────────────────────
def test_public_data_rows_are_reference_not_bookable():
    """A 2022 filming-location register is a real place, not a rental listing."""
    assert PublicDataProvider().default_kind == Kind.REFERENCE


def test_public_data_without_a_source_imports_nothing_and_says_why():
    """No CSV and no key must mean an empty import with an explanation — never
    invented rows to make the catalogue look complete."""
    import asyncio

    p = PublicDataProvider(csv_path=None, api_key="")
    asyncio.run(p.prepare(None))
    assert p.unavailable_reason
    assert asyncio.run(p.discover_ids(None)) == []


def test_public_data_normalizes_without_inventing_price_or_photos():
    from providers.base import RawListing

    p = PublicDataProvider()
    loc = p.normalize(RawListing(source_id="csv0", payload={
        "장소명": "남한산성 행궁",
        "소재지도로명주소": "경기 광주시 남한산성면 산성리 935-2",
        "장소설명": "사극 촬영이 잦은 조선시대 행궁",
        "위도": "37.4790", "경도": "127.1810",
    }))
    assert loc is not None
    assert loc.listing_kind == Kind.REFERENCE
    assert loc.price_per_hour == 0 and loc.price_per_day == 0
    assert loc.images == []                      # the dataset has no photographs
    assert loc.specs.window_direction == "확인 필요"
    assert loc.latitude and loc.longitude
    assert "확인" in loc.permit_summary


def test_unparseable_address_is_labelled_not_defaulted_to_seoul():
    assert _region_of("")[1] == "기타"
    assert _region_of("경기 광주시 남한산성면")[1] == "경기"


def test_stamp_records_provenance_on_every_row():
    from app.models.korean_locations import KoreanLocation, LocationSpec

    loc = KoreanLocation(
        id="pd_1", name="x", tagline="", region="서울", region_category="서울",
        category="자연/야외", price_per_hour=0, price_per_day=0, min_hours=0,
        rating=0, review_count=0, images=[],
        specs=LocationSpec(area_sqm=0, area_pyeong=0, ceiling_height_m=0,
                           window_direction="", natural_light_type="", golden_hour_window="",
                           power_capacity="", parking_spots=0, has_freight_elevator=False,
                           sound_recording_quality=""),
        tags=[], permit_summary="", citations=[],
    )
    stamped = stamp(loc, PublicDataProvider(), "csv7", "https://example.test/x")
    assert stamped.provider == "public_data"
    assert stamped.provider_listing_id == "csv7"
    assert stamped.rights_status == Rights.PUBLIC_OPEN_DATA
    assert stamped.listing_kind == Kind.REFERENCE   # provider default wins
    assert stamped.last_verified_at


# ── Cross-platform duplicates ───────────────────────────────────────────────
def _row(rid, provider, name, address="", lat=None, lon=None, phone=""):
    return {"id": rid, "provider": provider, "name": name, "address": address,
            "latitude": lat, "longitude": lon, "phone": phone}


ADDR = "서울특별시 성동구 성수이로 10"
ADDR_SHORT = "서울 성동구 성수이로 10"


def test_transliterated_name_at_one_address_is_one_venue():
    """The case that motivated this: "REAL HOUSE" on one platform is
    "리얼하우스" on another, and a bigram comparison scores those at zero."""
    a = _row("hp_1", "hourplace", "[성수] REAL HOUSE 스튜디오", ADDR, 37.5445, 127.0557)
    b = _row("sc_9", "spacecloud", "리얼하우스", ADDR_SHORT, 37.5446, 127.0558)
    assert is_same_venue(a, b)


def test_two_units_in_one_building_stay_separate():
    """Different floors of one address are different rentable spaces; merging
    them would hide one behind the other's price."""
    a = _row("hp_3", "hourplace", "성수 스튜디오 3층", ADDR, 37.5445, 127.0557)
    b = _row("sc_4", "spacecloud", "성수 스튜디오 5층", ADDR_SHORT, 37.5445, 127.0557)
    assert not is_same_venue(a, b)


def test_two_listings_on_the_same_platform_never_merge():
    a = _row("hp_1", "hourplace", "REAL HOUSE", ADDR, 37.5445, 127.0557)
    b = _row("hp_2", "hourplace", "REAL HOUSE", ADDR, 37.5445, 127.0557)
    assert not is_same_venue(a, b)


def test_distant_venues_never_merge():
    a = _row("hp_1", "hourplace", "한옥", "서울 종로구 1", 37.57, 126.99)
    b = _row("sc_1", "spacecloud", "한옥", "경기 양평군 3", 37.49, 127.49)
    assert not is_same_venue(a, b)


def test_matching_phone_alone_is_enough():
    a = _row("hp_1", "hourplace", "완전히 다른 이름", "", None, None, "02-123-4567")
    b = _row("sc_1", "spacecloud", "또 다른 이름", "", None, None, "021234567")
    assert is_same_venue(a, b)


def test_only_merged_rows_get_a_canonical_id():
    """A lone listing needs no canonical id, and writing one would burn a
    revision for every row in the catalogue on the first dedupe pass."""
    rows = [
        _row("hp_1", "hourplace", "REAL HOUSE", ADDR, 37.5445, 127.0557),
        _row("sc_9", "spacecloud", "리얼하우스", ADDR_SHORT, 37.5446, 127.0558),
        _row("hp_7", "hourplace", "무관한 한옥", "경기 양평군 3", 37.49, 127.49),
    ]
    mapping = assign_canonical(rows)
    assert set(mapping) == {"hp_1", "sc_9"}
    assert len(set(mapping.values())) == 1        # both point at one venue


def test_name_similarity_is_not_fooled_by_platform_decoration():
    assert name_similarity("[성수] 화이트 스튜디오 대관", "화이트스튜디오") > 0.6
    assert name_similarity("스튜디오 A", "스튜디오 B") < 0.62


# ── placehub ────────────────────────────────────────────────────────────────
def test_placehub_is_live_and_hourplace_keeps_its_prefix():
    """Both are rental platforms; their id prefixes must not collide or one
    would delist the other's listings."""
    from providers.placehub import PlacehubProvider

    assert "placehub" in PROVIDERS
    prefixes = [p.id_prefix for p in PROVIDERS.values()]
    assert len(prefixes) == len(set(prefixes)), f"duplicate id prefixes: {prefixes}"
    assert PlacehubProvider().id_prefix != PROVIDERS["hourplace"].id_prefix


def test_placehub_reads_the_sites_own_structured_data():
    """The listing pages publish schema.org Product blocks — data the site emits
    for machines. Parsing that beats scraping markup that was never meant to be
    read, and it is what keeps this adapter stable."""
    from providers.base import RawListing
    from providers.placehub import PlacehubProvider

    loc = PlacehubProvider().normalize(RawListing(source_id="76694", payload={
        "@type": "Product",
        "name": "당산역 100평 디모어스튜디오",
        "description": "당산역 초역세권, 4.8m의 높은 층고를 자랑합니다.\n3면 통창으로 자연광이 쏟아집니다. 100평 규모.",
        "category": "스튜디오",
        "areaServed": "서울 영등포구",
        "image": ["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"],
        "offers": {"price": 250000, "priceCurrency": "KRW"},
    }))
    assert loc is not None
    assert loc.price_per_hour == 250000
    assert loc.region_category == "서울"
    assert loc.category == "모던 스튜디오"
    assert loc.specs.ceiling_height_m == 4.8      # stated by the host, not guessed
    assert loc.specs.area_pyeong == 100.0
    assert loc.specs.natural_light_type == "자연광 우수"
    assert loc.citations[0].url.endswith("/places/76694")


def test_placehub_drops_a_listing_with_no_photo():
    """Same rule as every source: a listing with no photograph is dropped, not
    padded with a placeholder image."""
    from providers.base import RawListing
    from providers.placehub import PlacehubProvider

    assert PlacehubProvider().normalize(RawListing(source_id="1", payload={
        "name": "사진 없는 공간", "image": [], "offers": {"price": 10000},
    })) is None


def test_placehub_never_invents_a_daily_rate_or_a_window_bearing():
    """A daily price is not published, and a guessed window bearing would feed
    the solar engine confident nonsense."""
    from providers.base import RawListing
    from providers.placehub import PlacehubProvider

    loc = PlacehubProvider().normalize(RawListing(source_id="1", payload={
        "name": "연습실", "description": "지하 연습실입니다.", "category": "연습실",
        "areaServed": "서울 서초구", "image": ["https://cdn.test/a.jpg"],
        "offers": {"price": 10000},
    }))
    assert loc.price_per_day == 0
    assert loc.specs.window_direction == "확인 필요"
    assert loc.specs.ceiling_height_m == 0.0


def test_placehub_does_not_use_the_disallowed_api_path():
    """robots.txt disallows /api. The richer listing index lives there and is
    deliberately unused — the same line held against hourplace's api2."""
    import inspect

    from providers import placehub

    src = inspect.getsource(placehub)
    assert "/api" not in "".join(placehub.INDEX_PATHS)
    assert "robots" in src.lower()


# ── TourAPI: the live public source ─────────────────────────────────────────
def test_tourapi_is_a_live_api_not_a_frozen_file():
    """The filming-location CSVs on the portal are file-only and stale (서울시
    드라마CF was last modified in 2021). This one is an open API, so a crawl
    pass refreshes it like any other source."""
    from providers.tourapi import BASE, TourApiProvider

    p = TourApiProvider()
    assert p.rights == Rights.PUBLIC_OPEN_DATA
    assert p.default_kind == Kind.REFERENCE      # a public site is not bookable
    assert BASE.startswith("https://apis.data.go.kr")


def test_tourapi_without_a_key_imports_nothing_and_says_why(monkeypatch):
    import asyncio

    from providers.tourapi import TourApiProvider

    # The constructor falls back to the environment, and a developer machine
    # now has a real key — clear it so this tests the unconfigured path.
    monkeypatch.delenv("TOURAPI_KEY", raising=False)
    p = TourApiProvider(api_key="")
    asyncio.run(p.prepare(None))
    assert p.unavailable_reason and "TOURAPI_KEY" in p.unavailable_reason
    assert asyncio.run(p.discover_ids(None)) == []


def test_tourapi_carries_photographs_and_a_source_timestamp():
    """Photos are the reason this source is worth having — the filming-location
    registers have none, and a row with no image cannot be scouted or fed to the
    frame simulator. `modifiedtime` answers "when did the source last change?",
    which a crawl timestamp cannot."""
    from providers.base import RawListing
    from providers.tourapi import TourApiProvider

    loc = TourApiProvider().normalize(RawListing(source_id="126508", payload={
        "contentid": "126508", "contenttypeid": "12", "title": "남한산성",
        "addr1": "경기도 광주시 남한산성면 산성리", "tel": "031-743-6610",
        "firstimage": "https://tong.visitkorea.or.kr/a.jpg",
        "firstimage2": "https://tong.visitkorea.or.kr/b.jpg",
        "mapx": "127.1810", "mapy": "37.4790",
        "modifiedtime": "20260226103000", "_region": "경기",
    }))
    assert loc is not None
    assert len(loc.images) == 2
    assert loc.latitude == 37.4790 and loc.longitude == 127.1810   # mapy=lat
    assert loc.source_updated_at == "2026-02-26T10:30:00"
    assert loc.listing_kind == Kind.REFERENCE
    assert loc.price_per_hour == 0


def test_tourapi_drops_rows_with_no_photograph():
    from providers.base import RawListing
    from providers.tourapi import TourApiProvider

    assert TourApiProvider().normalize(RawListing(source_id="1", payload={
        "title": "사진 없는 관광지", "firstimage": "", "firstimage2": "", "_region": "서울",
    })) is None


# ── 국가유산청: the source that needs nothing from the user ─────────────────
def test_heritage_needs_no_key_or_download():
    """Every other public source is gated: the filming-location CSVs need a
    browser download, TourAPI needs 활용신청. This one answers unauthenticated,
    which is why it is the one that could simply be switched on."""
    import inspect

    from providers import heritage

    assert "serviceKey" not in inspect.getsource(heritage)
    assert heritage.HeritageProvider().rights == Rights.PUBLIC_OPEN_DATA


def test_heritage_rows_are_reference_and_say_a_permit_is_needed():
    """Filming on protected heritage land needs authority permission under the
    국가유산보호법. Presenting 숭례문 as bookable would be absurd."""
    from providers.base import RawListing
    from providers.heritage import HeritageProvider

    xml = """<result><latitude>37.559975</latitude><longitude>126.975312</longitude>
      <item><ccbaMnm1><![CDATA[서울 숭례문]]></ccbaMnm1>
      <ccbaLcad><![CDATA[서울 중구 세종대로 40]]></ccbaLcad>
      <ccmaName><![CDATA[국보]]></ccmaName>
      <imageUrl>http://www.khs.go.kr/unisearch/images/national_treasure/2685609.jpg</imageUrl>
      <content><![CDATA[조선시대 한양도성의 정문으로…]]></content></item></result>"""
    p = HeritageProvider()
    p._index["11_0000010000000_11"] = {"kd": "11", "asno": "0000010000000",
                                       "ctcd": "11", "region": "서울", "name": "서울 숭례문"}
    loc = p.normalize(RawListing(source_id="11_0000010000000_11",
                                 payload={"xml": xml, "meta": p._index["11_0000010000000_11"]}))
    assert loc is not None
    assert loc.listing_kind == Kind.REFERENCE
    assert loc.price_per_hour == 0
    assert loc.latitude and loc.longitude
    assert len(loc.images) == 1
    assert "허가" in loc.permit_summary and "국가유산보호법" in loc.permit_summary


def test_heritage_skips_museum_objects():
    """Movable property is a museum exhibit, not a location to scout."""
    from providers.heritage import SCOUTABLE_KINDS

    assert "17" not in SCOUTABLE_KINDS      # 무형유산
    assert "11" in SCOUTABLE_KINDS          # 국보(건조물)


def test_heritage_classifies_hanok_apart_from_open_ground():
    from providers.base import RawListing
    from providers.heritage import HeritageProvider

    def _mk(name):
        meta = {"kd": "11", "asno": "1", "ctcd": "11", "region": "서울", "name": name}
        xml = (f"<result><item><ccbaMnm1><![CDATA[{name}]]></ccbaMnm1>"
               "<ccbaLcad><![CDATA[서울 종로구 1]]></ccbaLcad>"
               "<ccmaName><![CDATA[보물]]></ccmaName>"
               "<imageUrl>http://x/y.jpg</imageUrl><content><![CDATA[]]></content></item></result>")
        return HeritageProvider().normalize(RawListing(source_id="s", payload={"xml": xml, "meta": meta}))

    assert _mk("안동 하회 충효당 고택").category == "전통 한옥"
    assert _mk("남한산성 성벽").category == "자연/야외"


# ── Manual Ver 4.4 conformance ──────────────────────────────────────────────
def test_tourapi_uses_ldong_codes_not_the_removed_areacode():
    """Manual Ver 4.4 (2026-02-10) deleted the areaCode parameter and the
    areaCode2 operation. The replacement uses different numbers entirely —
    부산 is 26 under 법정동, 6 under the old scheme — so a stale adapter does not
    error, it silently queries the wrong province."""
    import inspect

    from providers import tourapi

    # The request builder only — the module comment explains what areaCode was
    # and why it went, and that explanation should stay.
    src = inspect.getsource(tourapi.TourApiProvider.prepare)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "lDongRegnCd" in code
    assert "areaCode" not in code
    assert tourapi.LDONG_REGIONS["26"] == "부산"
    assert tourapi.LDONG_REGIONS["11"] == "서울"


def test_tourapi_sorts_so_every_row_has_a_photograph():
    """arrange=Q is 수정일순 *and* guarantees a 대표 이미지. The plain C sort
    returns rows with no photo, which this catalogue drops anyway — so the
    quota would be spent fetching rows destined for the bin."""
    import inspect

    from providers import tourapi

    assert "arrange=Q" in inspect.getsource(tourapi)


def test_tourapi_passes_the_encoding_key_verbatim():
    """The portal issues an *encoding* key: it arrives percent-encoded, ending
    %3D%3D. Handing it to a param encoder double-encodes the % and the API
    answers SERVICE_KEY_IS_NOT_REGISTERED_ERROR, which reads like a bad key."""
    import inspect

    from providers import tourapi

    src = inspect.getsource(tourapi.TourApiProvider.prepare)
    assert 'f"serviceKey={self.api_key}"' in src
    assert '"serviceKey": self.api_key' not in src


def test_tourapi_marks_photographs_that_may_not_be_altered():
    """cpyrhtDivCd=Type3 is 제1유형 + 변경금지 — free to display, forbidden to
    modify. It is 55% of the rows measured, and the frame simulator exists to
    modify photographs, so the flag has to reach the record."""
    from providers.base import RawListing
    from providers.tourapi import TourApiProvider

    def _mk(cpy):
        return TourApiProvider().normalize(RawListing(source_id="1", payload={
            "contentid": "1", "contenttypeid": "12", "title": "어떤 관광지",
            "addr1": "서울 종로구 1", "firstimage": "https://x/a.jpg",
            "mapx": "127.0", "mapy": "37.5", "_region": "서울", "cpyrhtDivCd": cpy,
        }))

    assert _mk("Type3").no_derivatives is True
    assert "변경금지" in _mk("Type3").tags
    assert _mk("Type1").no_derivatives is False


def test_the_simulator_refuses_a_no_derivatives_photograph():
    """Enforced at the API, not left to the UI to hide a button."""
    import inspect

    from app import main

    src = inspect.getsource(main.simulate_frame)
    assert "no_derivatives" in src
    assert "451" in src
