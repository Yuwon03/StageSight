import pytest

from app import catalog
from app.agent.tools.hourplace_ingest import (
    build_location,
    infer_window_direction,
    infer_natural_light,
    _category_of,
    _region_category,
)
from app.agent.tools.script_matcher import (
    analyze_script_and_match_locations,
    chat_with_script_ai,
    ChatRequest,
    ChatMessage,
    _split_scenes,
    _infer_scene_needs,
)


def _meta(**over):
    """A placeMeta payload shaped exactly like hourplace.co.kr's SSR __NEXT_DATA__."""
    base = {
        "id": 45083,
        "title": "[성수동] 성수 자연광 렌탈 스튜디오 / 25평 / 주차가능",
        "description": "자연광스튜디오 카테고리, 최대 3명 수용, 82.6㎡ 규모, 서울 성동구 위치의 촬영 공간입니다.",
        "region": "서울",
        "locality": "성동구",
        "lat": 37.5401, "lng": 127.0568,
        "floor": "12",
        "main_image_path": "place/user/1/a.jpg",
        "category_main": "자연광스튜디오",
        "address": "서울 성동구",
        "og_title": "성수 자연광 렌탈 스튜디오",
        "price_low": 55000, "price_high": 55000,
        "rating_value": 5, "rating_count": 4,
    }
    base.update(over)
    return base


def _gallery(n=3):
    return {
        "images": [f"https://img.hourplace.co.kr/p/{i}.jpg" for i in range(n)],
        "title": "성수 자연광 스튜디오",
        "captions": ["서향 통창으로 오후 햇살이 깊게 들어오는 스튜디오"],
    }


@pytest.fixture(autouse=True)
def _clean_catalog():
    catalog.replace_all([])
    yield
    catalog.replace_all([])


# ── Ingestion parsing ───────────────────────────────────────────────────────
def test_build_location_from_real_payload_shape():
    loc = build_location(45083, _meta(), _gallery())
    assert loc is not None
    assert loc.id == "hp_45083"
    assert loc.price_per_hour == 55000
    assert loc.region == "서울 성동구"
    assert loc.region_category == "서울"
    assert loc.specs.area_sqm == pytest.approx(82.6, abs=0.1)
    assert loc.specs.area_pyeong > 0
    assert len(loc.images) == 3
    assert loc.citations[0].verification_status == "LIVE"
    assert loc.citations[0].url == "https://hourplace.co.kr/place/45083"


def test_listing_without_any_photo_is_dropped():
    assert build_location(1, _meta(main_image_path=None), {"images": []}) is None


def test_missing_price_is_zero_not_invented():
    loc = build_location(2, _meta(price_low=None, price_high=None), _gallery())
    assert loc.price_per_hour == 0


def test_window_direction_inference():
    assert "서향" in infer_window_direction("서향 통창이 있는 공간")
    assert "동향" in infer_window_direction("자연광 시간대 오전 9시 ~ 오전 11시")
    assert "서향" in infer_window_direction("자연광 시간대 오후 2시 ~ 오후 5시")
    assert infer_window_direction("자연광이 좋은 스튜디오") == "자연광 (방향 미표기)"
    assert "암막" in infer_window_direction("암막 호리존 스튜디오")
    assert "확인 필요" in infer_window_direction("깨끗한 공간입니다")


def test_natural_light_and_direction_agree():
    """Both inferences read the same keyword set, so they must not contradict."""
    blob = "한강 창밖 전망의 사무실"
    assert infer_window_direction(blob) == "자연광 (방향 미표기)"
    assert "자연광" in infer_natural_light(blob)


def test_category_and_region_mapping():
    assert _category_of("한옥", "북촌 고택") == "전통 한옥"
    assert _category_of("자연광스튜디오", "성수 스튜디오") == "모던 스튜디오"
    assert _region_category("경기") == "경기"
    assert _region_category("알수없음") == "서울"


# ── Script parsing ──────────────────────────────────────────────────────────
def test_split_scenes_finds_each_header():
    scenes = _split_scenes(
        "[씬 14: 실내 다이닝룸 - 일몰]\n대화 장면.\n[씬 15: 야외 숲속 - 밤]\n추격 장면."
    )
    assert len(scenes) == 2
    assert scenes[0]["number"] == "씬 14"
    assert "다이닝룸" in scenes[0]["title"]


def test_scene_needs_inference():
    needs = _infer_scene_needs("서쪽 창문으로 쏟아지는 황금빛 일몰 역광", "실내 다이닝룸 - 일몰")
    assert needs["wants_west_window"] is True
    assert "골든아워" in needs["time_of_day"]

    outdoor = _infer_scene_needs("안개 낀 숲길로 뛰어 들어간다. 밤.", "야외 숲속")
    assert outdoor["category"] == "자연/야외"
    assert outdoor["night"] is True


# ── Matching against the real catalog ───────────────────────────────────────
@pytest.mark.asyncio
async def test_script_matching_uses_catalog_listings():
    catalog.replace_all([
        build_location(1, _meta(id=1, title="서향 자연광 스튜디오", price_low=50000), _gallery()),
        build_location(2, _meta(id=2, title="암막 호리존 스튜디오", category_main="호리존",
                                description="암막 스튜디오, 120㎡ 규모"), _gallery()),
    ])
    res = await analyze_script_and_match_locations("[씬 1: 실내 - 일몰]\n서쪽 창문 역광.")
    assert res.total_scenes_detected >= 1
    picked = res.scenes[0].primary_location
    assert catalog.by_id(picked.id) is not None, "matcher must only return catalog listings"


@pytest.mark.asyncio
async def test_empty_catalog_reports_honestly_instead_of_inventing():
    res = await analyze_script_and_match_locations("[씬 1: 실내]\n대화.")
    assert res.total_scenes_detected == 0
    assert res.scenes == []
    assert "수집" in res.overall_production_advice

    chat = await chat_with_script_ai(ChatRequest(messages=[ChatMessage(role="user", content="숲 느낌")]))
    assert chat.suggested_locations == []


@pytest.mark.asyncio
async def test_empty_catalog_english_chat_stays_in_english():
    chat = await chat_with_script_ai(
        ChatRequest(messages=[ChatMessage(role="user", content="Find a forest")], language="en")
    )
    assert chat.reply == "The real-location catalogue is empty, so no recommendation can be made."
    assert chat.suggested_locations == []


@pytest.mark.asyncio
async def test_chat_budget_filter_never_returns_over_budget():
    catalog.replace_all([
        build_location(1, _meta(id=1, price_low=50000), _gallery()),
        build_location(2, _meta(id=2, price_low=300000), _gallery()),
    ])
    res = await chat_with_script_ai(
        ChatRequest(messages=[ChatMessage(role="user", content="시간당 10만원 이하로 찾아줘")])
    )
    assert len(res.suggested_locations) >= 1
    for loc in res.suggested_locations:
        assert 0 < loc.price_per_hour <= 100000


def test_price_filter_excludes_unknown_price_listings():
    """A listing whose price could not be read must not surface as if it were free."""
    catalog.replace_all([build_location(1, _meta(price_low=None), _gallery())])
    assert catalog.search(max_price=100000) == []
    assert len(catalog.search()) == 1


def test_orbit_degrees_accept_fractional_drag_values():
    """Dragging the orbit widget yields fractional degrees; rejecting them
    surfaced to users as an HTTP 422 on generate."""
    from app.agent.tools.frame_simulator import FrameSimRequest

    req = FrameSimRequest(image_url="https://x/y.jpg", rotation=72.8, tilt=24.6, zoom=10.4)
    assert (req.rotation, req.tilt, req.zoom) == (73, 25, 10)
