import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.tools import location_localizer
from app.agent.tools.hourplace_ingest import build_location


def _location():
    return build_location(
        45083,
        {
            "id": 45083,
            "title": "[성수동] 서향 자연광 렌탈 스튜디오",
            "description": "오후 햇살이 깊게 들어오는 촬영 공간입니다.",
            "region": "서울",
            "locality": "성동구",
            "lat": 37.5401,
            "lng": 127.0568,
            "main_image_path": "place/user/1/a.jpg",
            "category_main": "자연광스튜디오",
            "address": "서울 성동구",
            "price_low": 55000,
            "rating_value": 5,
            "rating_count": 4,
        },
        {
            "images": ["https://img.hourplace.co.kr/p/1.jpg"],
            "title": "성수 자연광 스튜디오",
            "captions": ["서향 통창으로 오후 햇살이 깊게 들어오는 스튜디오"],
        },
    )


@pytest.mark.asyncio
async def test_english_copy_translates_text_but_preserves_source_facts():
    source = _location()
    translated = {
        "locations": [{
            "id": source.id,
            "name": "[Seongsu-dong] West-facing daylight rental studio",
            "tagline": "A filming space with deep afternoon sunlight.",
            "region": "Seongdong-gu, Seoul",
            "region_category": "Seoul",
            "category": "Modern studio",
            "tags": ["Daylight", "West-facing windows"],
            "specs": {
                "window_direction": "West-facing full-height windows",
                "natural_light_type": "Strong afternoon daylight",
                "golden_hour_window": "Approximately 4:00-6:00 PM",
                "power_capacity": "Confirm with the listing provider",
                "sound_recording_quality": "Confirm ambient noise on site",
            },
            "permit_summary": "Confirm filming terms with the host.",
            "citations": [{"title": "Hourplace live listing", "excerpt": "Seongdong-gu, Seoul"}],
        }]
    }
    response = SimpleNamespace(text=json.dumps(translated))
    location_localizer._CACHE.clear()

    with patch.object(location_localizer.settings, "GEMINI_API_KEY", "test-key"), \
         patch("google.genai.Client"), \
         patch.object(location_localizer, "try_models", return_value=response):
        result = (await location_localizer.localize_locations([source], "en", detail=True))[0]

    assert result.name.startswith("[Seongsu-dong]")
    assert result.region == "Seongdong-gu, Seoul"
    assert result.specs.window_direction.startswith("West-facing")
    assert result.citations[0].excerpt == "Seongdong-gu, Seoul"
    assert result.id == source.id
    assert result.price_per_hour == source.price_per_hour
    assert result.source_url == source.source_url
    assert result.images == source.images
    assert result.original_text.name == source.name
    assert result.original_text.region == source.region
    assert result.original_text.window_direction == source.specs.window_direction
    assert source.display_language == "ko"
    assert source.name.startswith("[성수동]")


@pytest.mark.asyncio
async def test_korean_request_never_calls_translation_model():
    source = _location()
    with patch.object(location_localizer, "try_models") as generate:
        result = await location_localizer.localize_locations([source], "ko")
    assert result == [source]
    generate.assert_not_called()


@pytest.mark.asyncio
async def test_localisation_keeps_catalogue_read_metadata():
    source = _location().model_copy(update={"is_new": True, "first_seen": "2026-09-05T00:00:00Z"})
    translated = {
        "locations": [{
            "id": source.id,
            "name": "Seongsu daylight studio",
            "tagline": "Filming space",
            "region": "Seongdong-gu, Seoul",
            "region_category": "Seoul",
            "category": "Modern studio",
            "tags": ["Daylight"],
            "specs": {"window_direction": "West-facing"},
        }]
    }
    location_localizer._CACHE.clear()
    with patch.object(location_localizer.settings, "GEMINI_API_KEY", "test-key"), \
         patch("google.genai.Client"), \
         patch.object(location_localizer, "try_models", return_value=SimpleNamespace(text=json.dumps(translated))):
        result = (await location_localizer.localize_locations([source], "en"))[0]
    assert result.is_new is True
    assert result.first_seen == "2026-09-05T00:00:00Z"
