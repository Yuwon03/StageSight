"""How the Gemini client is constructed.

Vertex AI is the production path — Gemini is reached with the runtime service
account rather than an API key. These tests cover the branch selection without
touching the network: a Vertex client that cannot actually call the API is only
detectable by making a call, so the factory probes once, and the whole point of
the fallback is that a failed probe must not take eight features down with it.
"""
import pytest

from app import gemini_models
from app.config import settings


class _FakeModels:
    def __init__(self, fail): self._fail, self.calls = fail, 0
    def generate_content(self, **kw):
        self.calls += 1
        if self._fail:
            raise RuntimeError("403 caller lacks aiplatform.user")
        return "ok"


class _FakeClient:
    def __init__(self, *, fail_probe=False, **kwargs):
        self.kwargs = kwargs
        self.models = _FakeModels(fail_probe)


@pytest.fixture(autouse=True)
def _reset():
    gemini_models.reset_genai_client()
    yield
    gemini_models.reset_genai_client()


def _patch(monkeypatch, *, fail_probe=False):
    """Stand in for `from google import genai` inside the factory."""
    built = []

    class _Genai:
        @staticmethod
        def Client(**kwargs):
            # Only the Vertex client is asked to fail its probe.
            c = _FakeClient(fail_probe=fail_probe and kwargs.get("vertexai"), **kwargs)
            built.append(c)
            return c

    import sys, types
    mod = types.ModuleType("google")
    mod.genai = _Genai
    monkeypatch.setitem(sys.modules, "google", mod)
    return built


def test_vertex_is_used_when_enabled(monkeypatch):
    built = _patch(monkeypatch)
    monkeypatch.setattr(settings, "USE_VERTEX_AI", True)
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setattr(settings, "VERTEX_LOCATION", "global")

    client = gemini_models.genai_client("some-key")

    # A key passed by a caller does not opt out of Vertex; it is only the
    # fallback credential.
    assert client.kwargs["vertexai"] is True
    assert client.kwargs["project"] == "proj"
    # Vertex serves this project's Gemini 3.x models only from `global`.
    assert client.kwargs["location"] == "global"
    assert "api_key" not in client.kwargs
    assert client.models.calls == 1, "the client must be probed before it is trusted"


def test_a_failed_probe_falls_back_to_the_api_key(monkeypatch):
    built = _patch(monkeypatch, fail_probe=True)
    monkeypatch.setattr(settings, "USE_VERTEX_AI", True)
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key-123")

    client = gemini_models.genai_client()

    assert client.kwargs == {"api_key": "key-123"}
    # And a request after the failure does not pay for another Vertex attempt:
    # the probe runs once per process, not once per render.
    gemini_models.genai_client()
    assert sum(1 for c in built if c.kwargs.get("vertexai")) == 1


def test_api_key_path_when_vertex_disabled(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "USE_VERTEX_AI", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key-123")

    client = gemini_models.genai_client()

    assert client.kwargs == {"api_key": "key-123"}
    assert client.models.calls == 0, "the API-key path needs no probe"


def test_a_caller_supplied_key_is_used_verbatim(monkeypatch):
    """The API-key client is per-call, as it was before the factory existed."""
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "USE_VERTEX_AI", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key-123")

    assert gemini_models.genai_client("other-key").kwargs == {"api_key": "other-key"}
    assert gemini_models.genai_client().kwargs == {"api_key": "key-123"}


def test_no_credentials_at_all_raises(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "USE_VERTEX_AI", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

    with pytest.raises(RuntimeError, match="No Gemini credentials"):
        gemini_models.genai_client()
