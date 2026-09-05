from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.tools import parallel_search


@pytest.mark.asyncio
async def test_english_summary_keeps_evidence_rules():
    captured = {}

    def generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text='{"permit_requirements":"Contact the authority.","curfew_hours":"","noise_limits":"","parking_and_loading":""}')

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    with patch.object(parallel_search.settings, "GEMINI_API_KEY", "test-key"), patch("google.genai.Client", return_value=client):
        result = await parallel_search._summarise_from_citations("Venue", "Seoul", [], "en")

    assert "Write all JSON values in English" in captured["contents"]
    assert "unsupported claims" in captured["contents"]
    assert result["curfew_hours"] == ""
