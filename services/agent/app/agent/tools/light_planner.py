"""Scene-specific light transport planning for fixed-camera relighting.

The image generator is good at following a concrete visual plan but has been
unreliable when it must infer light-source placement and transport from a time
label buried inside a long edit prompt. This module makes that reasoning an
explicit, cheap text-model step. It never edits an image; it returns observable
screen-space facts that the image model can render in one pass.

The planner is deliberately conservative. Unknown or occluded geometry stays
unknown, and a failed call returns an empty plan so the simulator falls back to
the deterministic physical prompt instead of fabricating evidence.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Tuple

from app.config import settings
from app.gemini_models import TEXT_MODELS, try_models

logger = logging.getLogger(__name__)

_CACHE: Dict[Tuple[str, str, int, str], str] = {}
_CACHE_MAX = 400

LIGHT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "target_emitters": {"type": "string"},
        "direct_receivers": {"type": "string"},
        "ambient_fill": {"type": "string"},
        "fixture_behavior": {"type": "string"},
        "cast_shadows": {"type": "string"},
        "indirect_and_reflections": {"type": "string"},
        "reference_cues_to_replace": {"type": "string"},
        "continuity_risks": {"type": "string"},
    },
    "required": [
        "target_emitters",
        "direct_receivers",
        "ambient_fill",
        "fixture_behavior",
        "cast_shadows",
        "indirect_and_reflections",
        "reference_cues_to_replace",
        "continuity_risks",
    ],
}

PLAN_PROMPT = """You are the lighting-continuity planner for a film location scout. Inspect the supplied
reference photograph but do not edit it. Produce a physically specific plan for re-photographing the exact
same fixed scene at the target hour below.

Use image-space language (frame-left, frame-right, foreground, rear wall, ceiling) and name surfaces and
fixtures actually visible in this photograph. Separate direct illumination, ambient fill, practical
fixtures, cast shadows, indirect bounce and reflections. State which existing bright patches, shadow
directions or fixture emissions belong to the reference hour and must be replaced. A target emitter may be
off-frame when supported by the visible scene; never invent a new visible window, lamp, room or object.

This is a technical plan, not a mood description or colour grade. Every field must describe a spatially
observable result. If evidence is insufficient, say "unknown" rather than guessing. Keep each field below
45 words.
"""


def format_light_plan(payload: Dict[str, str]) -> str:
    labels = (
        ("target emitters", "target_emitters"),
        ("directly lit surfaces", "direct_receivers"),
        ("ambient fill", "ambient_fill"),
        ("existing fixture behavior", "fixture_behavior"),
        ("cast shadows", "cast_shadows"),
        ("indirect bounce and reflections", "indirect_and_reflections"),
        ("reference-hour cues to replace", "reference_cues_to_replace"),
        ("continuity risks", "continuity_risks"),
    )
    return "\n".join(
        f"- {label}: {str(payload.get(key, '')).strip()}"
        for label, key in labels
        if str(payload.get(key, "")).strip()
    )


def get_light_plan(
    *,
    image_url: str,
    image_bytes: bytes,
    mime_type: str,
    light_phase: str,
    time_label: str,
    date_label: str,
    sun_altitude_deg: float,
    window_direction: str,
) -> str:
    """Return a cached screen-space lighting plan, or an empty safe fallback."""
    key = (
        image_url,
        light_phase,
        round(sun_altitude_deg / 5) * 5,
        window_direction,
    )
    if key in _CACHE:
        return _CACHE[key]

    if not settings.GEMINI_API_KEY:
        return ""

    target = (
        f"TARGET: {time_label} on {date_label}; phase={light_phase}; "
        f"sun_altitude={sun_altitude_deg:.1f} degrees; "
        f"known_aperture_bearing={window_direction or 'unknown'}."
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = try_models(
            TEXT_MODELS,
            lambda model: client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    f"{PLAN_PROMPT}\n\n{target}",
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LIGHT_PLAN_SCHEMA,
                    temperature=0.0,
                ),
            ),
        )
        plan = format_light_plan(json.loads(response.text))
    except Exception as exc:
        logger.warning("light planning failed for %s: %s", image_url[:60], exc)
        return ""

    if plan:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = plan
    return plan
