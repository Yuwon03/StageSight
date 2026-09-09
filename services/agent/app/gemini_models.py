"""
Central list of Gemini model ids, with fallbacks.

Google retires model ids without warning — `gemini-2.5-flash` started returning
404 "no longer available to new users" mid-project and silently degraded script
matching to the deterministic path. Keeping the candidates in one place means a
retirement is a one-line fix, and callers try the list in order.
"""
import logging
import threading
from typing import Any, Callable, List, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

# Measured 2026-09-03 against this project's own prompt shapes, not chosen by
# version number: 3.8 and 3.6 are priced identically ($0.75/$3.75 per 1M in/out),
# 3.8 is faster (median 2.7s vs 3.4s over four calls each), and it held the
# grounding rule better — given evidence that only described a noise standard,
# 3.6 also filled in a filming curfew that nothing in the evidence supported.
# That refusal is exactly what the permit summariser depends on.
TEXT_MODELS: List[str] = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
]

# Two tiers the user picks between, chosen by measurement rather than by version
# number. Four image models were scored over the same 24 cells (golden / night /
# low / orbit45 across six space types) on 2026-09-03:
#
#   model                    $/1K img   overall  identity  light  camera
#   3.1-flash-lite-image      0.0336     3.527     4.00     3.17   3.04
#   2.5-flash-image           0.0390     3.507     3.96     3.17   3.04
#   3.1-flash-image           0.0670     3.497     3.83     3.12   3.25
#   3-pro-image               0.1340     3.450     3.92     3.00   3.04
#
# The spread is 0.077 across a 4x price range — noise at n=24 — and the most
# expensive model scored *lowest*. So the tiers are NOT "fast vs accurate":
# nothing here measures as more accurate. What does differ, and is verifiable,
# is latency and output resolution:
#
#   FAST   3.1-flash-lite @ 1K   ~11s   $0.034   cheaper and quicker than the
#                                                model this replaced, no measured
#                                                quality loss
#   DETAIL 3-pro @ 2K            ~30s   $0.134   2528x1686 instead of ~1024 wide,
#                                                which is real for a scout zooming
#                                                into a space — but do not sell it
#                                                as a better render, because it is
#                                                measurably not one
IMAGE_TIERS: dict = {
    "fast":   {"model": "gemini-3.1-flash-lite-image", "image_size": None},
    "detail": {"model": "gemini-3-pro-image",          "image_size": "2K"},
}
DEFAULT_IMAGE_TIER = "fast"

# Multi-reference and high-thinking relighting experiments use the generalist
# model explicitly. Google's current model guide says Lite is optimized for
# cost/latency and is not optimized for multi-reference or sequential editing.
RELIGHT_WORKFLOW_MODEL = "gemini-3.1-flash-image"

# NOT upgraded alongside the text model. The frame prompt (v7) was tuned across
# ~760 scored generations against gemini-2.5-flash-image specifically; every
# finding in the CLAUDE.md table is measured on it. Swapping the image model
# invalidates that work, so it stays until a fresh eval round justifies a move.
# The old "gemini-3.6-flash-image" fallback never existed — it is not in the
# models list — so a real id replaces it.
IMAGE_MODELS: List[str] = [
    "gemini-3.1-flash-lite-image",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
]


# --- How Gemini is reached -------------------------------------------------
#
# Vertex AI on Google Cloud, authenticated by the runtime service account. The
# API-key path (Gemini Developer API) stays as a fallback so local development
# and the evaluation harness run without cloud credentials.
#
# The fallback is not decorative. Constructing a Vertex client succeeds even
# when the service account cannot actually call the API — the failure only
# surfaces on the first generate_content, which in this codebase is inside a
# user request. So the first client build makes one tiny probe call: if Vertex
# is not usable here, this process says so once in the log and serves every
# subsequent request over the API key instead of failing eight features at once.
#
# Vertex needs `global`, not the Cloud Run region — see config.VERTEX_LOCATION.

_client_lock = threading.Lock()
_vertex_client: Any | None = None
_vertex_unusable = False


def _probe(client: Any) -> None:
    """Cheapest possible call that proves this client can actually generate."""
    client.models.generate_content(model=TEXT_MODELS[0], contents="ok")


def genai_client(api_key: str | None = None) -> Any:
    """Return a Gemini client: Vertex AI when usable, the API key otherwise.

    Only the Vertex client is cached, because only it costs a probe call. The
    API-key client is rebuilt per call exactly as every call site used to build
    it, so `api_key` keeps meaning what it meant before, and nothing is shared
    between callers that did not share it already.
    """
    global _vertex_client, _vertex_unusable
    from google import genai

    if settings.USE_VERTEX_AI and settings.GOOGLE_CLOUD_PROJECT and not _vertex_unusable:
        with _client_lock:
            if _vertex_client is not None:
                return _vertex_client
            if not _vertex_unusable:
                try:
                    client = genai.Client(
                        vertexai=True,
                        project=settings.GOOGLE_CLOUD_PROJECT,
                        location=settings.VERTEX_LOCATION,
                    )
                    _probe(client)
                    logger.info(
                        "Gemini via Vertex AI (project=%s, location=%s)",
                        settings.GOOGLE_CLOUD_PROJECT,
                        settings.VERTEX_LOCATION,
                    )
                    _vertex_client = client
                    return client
                except Exception as e:
                    _vertex_unusable = True
                    logger.warning(
                        "Vertex AI unusable (%s); falling back to the Gemini API key",
                        str(e)[:200],
                    )

    key = api_key or settings.GEMINI_API_KEY
    if not key:
        raise RuntimeError(
            "No Gemini credentials. Set USE_VERTEX_AI=true with a service "
            "account that has roles/aiplatform.user, or set GEMINI_API_KEY."
        )
    return genai.Client(api_key=key)


def reset_genai_client() -> None:
    """Drop the cached Vertex client. Tests only."""
    global _vertex_client, _vertex_unusable
    with _client_lock:
        _vertex_client = None
        _vertex_unusable = False


T = TypeVar("T")


def try_models(models: List[str], call: Callable[[str], T]) -> T:
    """Run `call(model)` against each candidate until one succeeds."""
    last: Exception | None = None
    for name in models:
        try:
            return call(name)
        except Exception as e:
            last = e
            logger.warning(f"Gemini model {name} unavailable: {str(e)[:160]}")
    raise last if last else RuntimeError("no models configured")
