"""
Gemini-as-judge for (original, generated, requested settings) triples.

Design decisions that matter for trustworthiness:

* BOTH images go in the same call, labelled, so the judge compares rather than
  rating a single image against an imagined ideal.
* The rubric is anchored: each score has a written definition of what 1, 3 and 5
  look like. Unanchored 1-5 scales collapse to "4" for everything.
* Every score must be accompanied by a one-line observation naming something
  actually visible. Forcing evidence is the cheapest defence against a judge that
  is agreeing rather than looking.
* The judge is told the DEFAULT is failure and that most renders have at least
  one real problem. Left neutral, VLM judges skew generous.
* Scores are returned via a forced JSON schema, so parsing never fails silently.

The judge is one signal, not the truth. evaluation/metrics.py cross-checks it,
and disagreements between the two are the most informative rows in a run.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Dict, Optional

from app.config import settings
from app.gemini_models import TEXT_MODELS

logger = logging.getLogger(__name__)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "identity": {"type": "integer"},
        "identity_note": {"type": "string"},
        "light": {"type": "integer"},
        "light_note": {"type": "string"},
        "camera": {"type": "integer"},
        "camera_note": {"type": "string"},
        "framing": {"type": "integer"},
        "framing_note": {"type": "string"},
        "realism": {"type": "integer"},
        "realism_note": {"type": "string"},
        "returned_input_unchanged": {"type": "boolean"},
        "invented_duplicate_geometry": {"type": "boolean"},
        "worst_problem": {"type": "string"},
    },
    "required": [
        "identity", "identity_note", "light", "light_note", "camera", "camera_note",
        "framing", "framing_note", "realism", "realism_note",
        "returned_input_unchanged", "invented_duplicate_geometry", "worst_problem",
    ],
}

RUBRIC = """You are grading an image-editing system for a film location-scouting tool. It takes a real
photograph of a rentable space (IMAGE A) and must re-render it under a requested camera setup and time of
day (IMAGE B). Your grading decides whether the system ships, so grade like a sceptical reviewer, not a
supportive one.

DEFAULT TO A LOW SCORE. Most renders have at least one real problem. A 5 is rare. If you are unsure whether
something changed, it did not change.

Score each dimension 1-5 using these anchors:

IDENTITY — is IMAGE B unmistakably the SAME physical place as IMAGE A?
  5 = same room; same wall/floor materials and colours, same furniture and fixtures, same window and door
      positions, same proportions. A location scout would recognise it instantly.
  3 = clearly related but something concrete is wrong — a fixture changed shape, a material shifted, the
      room proportions altered.
  1 = a different place, or the key furniture/architecture has been replaced.

LIGHT — does the illumination match the REQUESTED time of day and sun position?
  5 = unmistakable at a glance. Night reads as night (dark, artificial light only, black outside);
      golden hour has low warm raking light and long shadows; midday has short hard shadows.
  3 = the direction is right but weak — a tint rather than a genuine change of lighting condition.
  1 = the light is unchanged from IMAGE A, or contradicts the request (a "night" render that is daylit).

CAMERA — was the camera actually MOVED as requested (height / tilt / orbit position)?
  5 = the viewpoint is demonstrably different in the way asked: for a high angle you now look down and see
      floor and the tops of objects; for an orbit the subject is seen from a new side with a different
      background behind it.
  3 = a partial move — some parallax or tilt, but less than requested.
  1 = the camera did not move. Same viewpoint as IMAGE A, possibly just cropped or panned.
  If the request was rotation 0 and tilt 0, score 5 only if the viewpoint is correctly UNCHANGED.

FRAMING — did the field of view change as the lens requested?
  5 = a wide request clearly shows MORE of the space than IMAGE A (more floor, ceiling, side walls, objects
      smaller); a telephoto request clearly shows LESS (tighter, objects larger).
  3 = a small change in the right direction.
  1 = no change in field of view, or it changed the wrong way.
  If the lens was "as-shot / normal", score 5 only if the field of view is correctly about the same.

REALISM — does IMAGE B look like a real photograph?
  5 = photographic: real materials, natural imperfection, plausible optics and light falloff.
  3 = slightly synthetic — plasticky surfaces, over-clean, or a CGI-render look.
  1 = obviously generated: melted geometry, impossible structures, garbled detail.

Also answer two yes/no questions honestly:
  returned_input_unchanged — is IMAGE B essentially IMAGE A with at most a colour tint, despite a camera or
    lens change being requested? (If no camera/lens change was requested, answer false.)
  invented_duplicate_geometry — did it mirror or duplicate parts of the room, or add windows, doors, rooms
    or fixtures that are not in IMAGE A and could not plausibly be there?

Every *_note must cite something you can actually SEE (name the object, wall, shadow or light source).
Notes like "looks good" are worthless. Keep each note under 25 words.
worst_problem: the single biggest defect in one short sentence, or "none" if it is genuinely clean."""


def _describe_request(case: Dict) -> str:
    rot = int(case.get("rotation", 0)) % 360
    tilt = int(case.get("tilt", 0))
    zoom = int(case.get("zoom", 10))
    focal = case.get("focal_mm", "?")

    if rot < 12 or rot >= 348:
        rot_txt = "camera stays on the same side of the space as the original (no orbit)"
    else:
        side = "right" if rot < 180 else "left"
        amt = rot if rot < 180 else 360 - rot
        rot_txt = f"camera walks {amt}° around the space to the {side} and shoots from there"

    if tilt >= 78:
        tilt_txt = "bird's-eye: camera at the ceiling pointing straight down, floor fills the frame"
    elif tilt >= 22:
        tilt_txt = f"high angle: camera raised well above head height, tilted down {tilt}°"
    elif tilt > -8:
        tilt_txt = "eye level, roughly 1.6 m, lens level with the horizon"
    elif tilt > -50:
        tilt_txt = f"low angle: camera near knee height, tilted up {abs(tilt)}°"
    else:
        tilt_txt = "worm's-eye: camera on the floor pointing steeply up, ceiling dominates"

    if zoom <= 4:
        lens_txt = f"{focal}mm ultra-wide: must show MUCH MORE of the space than the original"
    elif zoom <= 8:
        lens_txt = f"{focal}mm wide: must show noticeably more of the space than the original"
    elif zoom <= 12:
        lens_txt = f"{focal}mm normal: field of view about the same as the original"
    elif zoom <= 16:
        lens_txt = f"{focal}mm short telephoto: tighter crop, showing less of the space"
    else:
        lens_txt = f"{focal}mm telephoto: much tighter crop on the centre"

    return (
        f"- CAMERA POSITION: {rot_txt}\n"
        f"- CAMERA HEIGHT/TILT: {tilt_txt}\n"
        f"- LENS: {lens_txt}\n"
        f"- TIME OF DAY: {case.get('time_label')} on {case.get('date_label')}, "
        f"light phase '{case.get('light_phase')}', sun altitude {case.get('sun_altitude_deg')}° "
        f"({'below' if float(case.get('sun_altitude_deg', 0)) < 0 else 'above'} the horizon)\n"
        f"- SPACE TYPE: {case.get('space_category') or 'unknown'}"
    )


def judge_pair(src_bytes: bytes, out_bytes: bytes, case: Dict) -> Optional[Dict]:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        return None

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = (
        f"{RUBRIC}\n\n"
        f"THE SYSTEM WAS ASKED FOR:\n{_describe_request(case)}\n\n"
        "IMAGE A is the original photograph. IMAGE B is what the system produced."
    )

    contents = [
        "IMAGE A (original):",
        types.Part.from_bytes(data=src_bytes, mime_type="image/jpeg"),
        "IMAGE B (generated):",
        types.Part.from_bytes(data=out_bytes, mime_type="image/png"),
        prompt,
    ]

    try:
        last_error = None
        for model_name in TEXT_MODELS:
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JUDGE_SCHEMA,
                        temperature=0.0,  # reduces variance; does not make the judge deterministic
                    ),
                )
                payload = json.loads(resp.text)
                payload["_judge_model"] = model_name
                return payload
            except Exception as exc:
                last_error = exc
                logger.warning("judge model %s failed: %s", model_name, exc)
        raise last_error if last_error else RuntimeError("no judge models configured")
    except Exception as e:
        logger.warning(f"judge failed: {e}")
        return None


SCORE_KEYS = ("identity", "light", "camera", "framing", "realism")


def overall(scores: Dict) -> float:
    """Identity is weighted hardest: a beautiful render of the wrong place is
    worse than useless to a scout who then drives there."""
    weights = {"identity": 0.32, "light": 0.22, "camera": 0.22, "framing": 0.14, "realism": 0.10}
    return round(sum(scores.get(k, 0) * w for k, w in weights.items()), 3)
