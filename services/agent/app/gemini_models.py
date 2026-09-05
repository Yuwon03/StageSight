"""
Central list of Gemini model ids, with fallbacks.

Google retires model ids without warning — `gemini-2.5-flash` started returning
404 "no longer available to new users" mid-project and silently degraded script
matching to the deterministic path. Keeping the candidates in one place means a
retirement is a one-line fix, and callers try the list in order.
"""
import logging
from typing import Callable, List, TypeVar

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
