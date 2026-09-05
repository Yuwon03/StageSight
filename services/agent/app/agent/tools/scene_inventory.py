"""
A one-off reading of what is actually in a listing photo, cached per image URL.

Why this exists: telling an image model to "invent the unseen part plausibly" is
an abstraction it cannot ground, which is why large orbits came back as the
original frontal view and wide shots came back mirrored. Giving it the room's
own materials by name — "newly revealed area is plain continuation of the pale
oak floor and white plaster walls" — turns an abstraction into a description it
can draw.

It also lets the prompt stop guessing: whether the space is tight (a 90° orbit
in a 3 m room puts the photographer's back against a wall, which is a different
photograph and needs saying), whether there are windows at all (a windowless
studio must not sprout one at night), and what the light in the source photo
already is (if it already matches the request, the right answer is to leave the
light alone rather than "change" it).

One cheap text call per photo, cached for the process lifetime.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from app.config import settings
from app.gemini_models import TEXT_MODELS, try_models

logger = logging.getLogger(__name__)

_CACHE: Dict[str, "SceneInventory"] = {}
_CACHE_MAX = 400

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "space_kind": {"type": "string", "enum": ["interior", "exterior", "hanok"]},
        "tightness": {"type": "string", "enum": ["tight", "normal", "open"]},
        "ceiling_class": {"type": "string", "enum": ["low", "standard", "tall", "none", "unknown"]},
        "floor": {"type": "string"},
        "walls": {"type": "string"},
        "ceiling": {"type": "string"},
        "openings": {"type": "array", "items": {"type": "string"}},
        "fixtures": {"type": "array", "items": {"type": "string"}},
        "anchors": {"type": "array", "items": {"type": "string"}},
        "input_light_phase": {
            "type": "string",
            "enum": ["night", "blue_hour", "golden_hour", "morning", "midday", "afternoon", "artificial"],
        },
        "behind_camera": {"type": "string"},
    },
    "required": ["space_kind", "tightness", "ceiling_class", "floor", "walls", "ceiling",
                 "openings", "fixtures", "anchors", "input_light_phase", "behind_camera"],
}

PROMPT = """You are preparing a location-continuity sheet for a film scout, from one photograph of a
rentable space. Describe only what you can actually see. Be terse and concrete — these notes are read by
another system, not by a person.

space_kind      interior | exterior | hanok (traditional Korean timber building)
tightness       tight  = a small room; a camera could not walk far without hitting a wall
                normal = an ordinary room with space to move around
                open   = a hall, a large studio, or outdoors
ceiling_class   low | standard | tall | none (outdoors) | unknown
floor           material and colour, 6 words max, e.g. "pale oak planks, running left-right"
walls           material and colour, 6 words max
ceiling         material and colour, 6 words max, or "open sky" outdoors
openings        one entry per window/door/opening you can see: what it is and which wall it is in.
                Empty list if the space genuinely has none visible.
fixtures        visible lamps, pendants, spotlights, practicals. Empty list if none visible.
anchors         5-8 specific objects or features a scout would use to recognise this exact venue again
input_light_phase  which of these the CURRENT photo was taken in: night, blue_hour, golden_hour,
                morning, midday, afternoon, or artificial (studio lighting, no daylight visible)
behind_camera   one sentence: what the wall or area BEHIND the photographer most plausibly is, judging
                from this room's materials and layout. This is an informed guess; make it plausible."""


@dataclass
class SceneInventory:
    space_kind: str = "interior"
    tightness: str = "normal"
    ceiling_class: str = "unknown"
    floor: str = ""
    walls: str = ""
    ceiling: str = ""
    openings: List[str] = field(default_factory=list)
    fixtures: List[str] = field(default_factory=list)
    anchors: List[str] = field(default_factory=list)
    input_light_phase: str = ""
    behind_camera: str = ""
    ok: bool = False

    @property
    def has_openings(self) -> bool:
        return bool(self.openings)

    def materials_phrase(self) -> str:
        bits = [b for b in (self.floor, self.walls, self.ceiling) if b]
        return ", ".join(bits) if bits else "the room's own materials"

    def anchor_lines(self, limit: int = 6) -> str:
        return "\n".join(f"  - {a}" for a in self.anchors[:limit]) if self.anchors else ""


def _fetch(url: str) -> Optional[bytes]:
    try:
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=4.0), follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": BROWSER_UA})
            return r.content if r.status_code == 200 else None
    except Exception:
        return None


def get_inventory(image_url: str, image_bytes: Optional[bytes] = None) -> SceneInventory:
    """Never raises: a failed reading degrades to empty defaults, and the prompt
    falls back to its generic wording rather than the request failing."""
    if image_url in _CACHE:
        return _CACHE[image_url]

    inv = SceneInventory()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        return inv

    data = image_bytes or _fetch(image_url)
    if not data:
        return inv

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        resp = try_models(
            TEXT_MODELS,
            lambda m: client.models.generate_content(
                model=m,
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=INVENTORY_SCHEMA,
                    temperature=0.0,
                ),
            ),
        )
        payload = json.loads(resp.text)
        inv = SceneInventory(**payload, ok=True)
    except Exception as e:
        logger.warning(f"scene inventory failed for {image_url[:60]}: {e}")
        return inv

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[image_url] = inv
    return inv
