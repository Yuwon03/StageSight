"""
AI frame simulator: re-renders a location photo under a different time-of-day,
sunlight direction, and lens focal length using Gemini image generation
(gemini-2.5-flash-image). Falls back with a clear 'unavailable' signal when
GEMINI_API_KEY is not configured, so the frontend can switch to its
physics-based approximation and label it honestly.
"""
import os
import asyncio
import base64
import hashlib
import logging
from typing import Optional, Dict, Tuple

import httpx
from pydantic import BaseModel, field_validator

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory result cache: identical (image, lens, time-bucket, phase) requests
# are served instantly and don't burn Gemini quota.
_FRAME_CACHE: Dict[Tuple[str, int, str, str, str], str] = {}
_CACHE_MAX = 200

from app.gemini_models import (  # noqa: E402
    IMAGE_MODELS,
    IMAGE_TIERS,
    DEFAULT_IMAGE_TIER,
    RELIGHT_WORKFLOW_MODEL,
)
from app.agent.tools.frame_prompt import (  # noqa: E402
    build_prompt,
    build_physical_relight_prompt,
    build_relight_refinement_prompt,
    PROMPT_VERSION,
    orbit_amount,
)
from app.agent.tools.light_planner import get_light_plan  # noqa: E402
from app.agent.tools.scene_inventory import get_inventory  # noqa: E402
from app.viewpoint_check import camera_actually_moved  # noqa: E402


class FrameSimRequest(BaseModel):
    image_url: str
    time_label: str = "17:30"           # "HH:MM" local time being simulated
    light_phase: str = "golden_hour"    # night | blue_hour | golden_hour | morning | midday | afternoon
    phase_description: str = ""         # human-readable Korean description from the solar engine
    window_direction: str = ""          # e.g. "남서향 (220°)"
    date_label: str = ""                # e.g. "2026-06-21"
    # Orbit camera rig — continuous values from the 3D angle picker
    rotation: int = 0                   # 0-359, orbit around the subject (0 = original viewpoint)
    tilt: int = 0                       # -90 (worm, on the floor) … 0 (eye) … +90 (bird, straight down)
    zoom: int = 10                      # 1 (ultra-wide) … 10 (as-shot) … 20 (telephoto)
    focal_length_mm: int = 0            # optional override; derived from zoom when 0
    sun_altitude_deg: float = 0.0       # from the client solar engine; negative means below horizon
    space_category: str = ""            # listing category, e.g. "자연/야외" — picks the light vocabulary
    bypass_cache: bool = False          # evaluation harness: a prompt change must be re-rendered, not replayed
    prompt_version: str = "v2"          # "v1" keeps the pre-rewrite wording, for A/B rounds
    # Which listing this frame belongs to, so the API can check its licence
    # before spending a Gemini call on a photograph it may not alter.
    location_id: str = ""
    # Override the image model for one request. Exists so evaluation/run_eval
    # can A/B two models over the same listings and cases; production leaves it
    # empty and uses the registry order.
    image_model: str = ""
    # Which tier the user chose: "fast" (default) or "detail". See
    # app/gemini_models.py for what each maps to and why — the difference is
    # latency and output resolution, not measured accuracy.
    image_tier: str = DEFAULT_IMAGE_TIER
    # Evaluation/rollout switch. "standard" is the shipped v11-style single
    # prompt; the other values stay explicit until a paired round wins.
    render_strategy: str = "standard"  # standard | physical | planned | iterative

    @field_validator("rotation", "tilt", "zoom", "focal_length_mm", mode="before")
    @classmethod
    def _round_degrees(cls, v):
        """A drag on the orbit widget yields fractional degrees (72.8°). Round them
        instead of rejecting the request — this used to surface as a 422."""
        if isinstance(v, float):
            return round(v)
        return v

    @field_validator("render_strategy")
    @classmethod
    def _known_render_strategy(cls, value: str) -> str:
        allowed = {"standard", "physical", "planned", "iterative"}
        if value not in allowed:
            raise ValueError(f"render_strategy must be one of {sorted(allowed)}")
        return value


class FrameSimResponse(BaseModel):
    image_data_url: str
    cached: bool = False
    model: str = ""
    note: str = ""
    focal_length_mm: int = 35
    # None when a camera move was not requested, or when the check could not
    # decide. False means the render is explained as a crop or pan of the source
    # rather than a camera that travelled — measured, not guessed. A tight room
    # genuinely cannot be orbited far, so the UI says so instead of pretending.
    camera_moved: Optional[bool] = None
    # The prompt revision this render actually came from. uvicorn runs without
    # --reload, so editing frame_prompt.py changes nothing until the server is
    # restarted — an eval round can silently measure the previous prompt and be
    # attributed to the new one. run_eval refuses to start on a mismatch.
    prompt_version: str = ""
    # Which tier produced this frame, echoed so the UI can label it.
    image_tier: str = ""
    render_strategy: str = "standard"
    prompt_fingerprint: str = ""


def scene_kind(category: str) -> str:
    """Outdoor spaces need different light vocabulary — an exterior shot has no
    'windows going dark', it has a sky. Guessing wrong makes night renders read
    as an interior lit through glass."""
    if any(k in category for k in ("자연", "야외")):
        return "exterior"
    if any(k in category for k in ("한옥", "고택")):
        return "hanok"
    return "interior"


# Per-phase light, written separately for interiors and exteriors. A scout
# flipping between 09:00 and 22:00 has to SEE night fall, not a slight tint shift.
EXTERIOR_PHASE_PROMPTS = {
    "night": (
        "FULL NIGHT outdoors, well after sunset. The sky is BLACK with no daylight and no glow on the "
        "horizon. Nothing is lit by the sun. The only illumination is artificial — street lamps, garden "
        "or facade lighting, windows glowing warm from inside buildings — pooling on surfaces with deep "
        "unlit darkness between them. Distant background falls away into black. Low overall exposure, "
        "high contrast between lit pools and shadow. This must read instantly as a night photograph."
    ),
    "blue_hour": (
        "BLUE HOUR outdoors, about 20 minutes after sunset. The sky is a deep saturated indigo with no "
        "sun and no warm horizon band. Artificial lights read warm orange against that cold blue. "
        "Landscape is still readable but dim."
    ),
    "golden_hour": (
        "GOLDEN HOUR outdoors, sun low near the horizon. Warm orange-gold light rakes across the scene "
        "at a shallow angle, throwing LONG shadows across the ground. Strong warm rim-light on edges "
        "facing the sun; shaded sides go cool blue by contrast. Sky warms toward amber near the horizon."
    ),
    "morning": (
        "CLEAR MORNING outdoors, sun low-to-mid in the east. Crisp, slightly cool daylight, bright blue "
        "sky, medium-length shadows angled across the ground. Fresh and airy — unmistakably daytime."
    ),
    "midday": (
        "BRIGHT MIDDAY outdoors, sun high overhead. Strong top-down sunlight, SHORT hard-edged shadows "
        "directly beneath objects, bright blue sky, high contrast. Unmistakably the middle of a sunny day."
    ),
    "afternoon": (
        "MID-TO-LATE AFTERNOON outdoors, sun descending. Warm slanted sunlight, shadows lengthening "
        "across the ground, colour temperature warming toward amber but not yet golden hour. Clearly daylight."
    ),
}

PHASE_PROMPTS = {
    "night": (
        "FULL NIGHT, well after sunset. There is NO daylight anywhere in the frame. Outside every "
        "window and beyond every opening it is BLACK — night sky, no blue, no glow on the horizon. "
        "The only illumination is artificial: warm interior lamps, ceiling fixtures, or street lighting, "
        "casting pooled light with deep unlit shadow between them. Overall exposure is LOW and the "
        "contrast between lit pools and black shadow is high. Any exterior surface is lit only by "
        "artificial light falling on it. This must read instantly as a night photograph."
    ),
    "blue_hour": (
        "BLUE HOUR, roughly 20 minutes after sunset. The sky and everything outdoors is a deep saturated "
        "indigo-blue with no sun visible and no warm horizon glow. Interior/artificial lights read warm "
        "orange against that cold blue, creating a strong warm-cool split. Exposure is dim but not black."
    ),
    "golden_hour": (
        "GOLDEN HOUR, sun low near the horizon. Intense warm orange-gold directional sunlight rakes across "
        "the scene at a shallow angle, throwing LONG dramatic shadows well across the floor and ground. "
        "Strong warm rim-light on edges facing the sun; the shaded side falls into cool blue by contrast. "
        "The whole frame is saturated warm amber."
    ),
    "morning": (
        "CLEAR MORNING, sun low-to-mid in the east. Crisp, slightly cool neutral daylight with clean bright "
        "highlights and medium-length soft-edged shadows angled across the scene. Bright, fresh and airy — "
        "unmistakably daytime."
    ),
    "midday": (
        "BRIGHT MIDDAY, sun high overhead. Strong top-down sunlight, SHORT hard-edged shadows pooled directly "
        "beneath objects, high contrast, neutral-to-cool white balance, blown bright sky outside the windows. "
        "Unmistakably the middle of a sunny day."
    ),
    "afternoon": (
        "MID-TO-LATE AFTERNOON, sun descending. Warm directional sunlight entering at a noticeably slanted "
        "angle, shadows lengthening across the floor and ground, colour temperature warming toward amber but "
        "not yet golden hour. Clearly still daylight."
    ),
}

# Standard cinematography camera angles. Each moves the virtual camera in the
# SAME room — the space itself must stay recognizably identical.
# ── Continuous orbit-rig → cinematography language ──────────────────────────
# The picker gives us rotation/tilt/zoom. Gemini responds far better to concrete
# camera-department language (height in metres, tilt in degrees, what fills the
# frame) than to abstract numbers, so we translate rather than pass through.

def zoom_to_focal_mm(zoom: int) -> int:
    """Zoom 1→16mm ultra-wide, 10→35mm normal, 20→85mm telephoto (exponential)."""
    z = max(1, min(20, zoom))
    return int(round(16 * (85 / 16) ** ((z - 1) / 19)))


def describe_tilt(tilt: int) -> str:
    t = max(-90, min(90, tilt))
    if t >= 78:
        return (
            "BIRD'S EYE — REPOSITION THE CAMERA COMPLETELY. Place it at the ceiling pointing STRAIGHT "
            "DOWN at the floor: a 90-degree vertical top-down aerial shot. The floor fills the ENTIRE "
            "frame. Every object is seen from directly overhead — the TOP of the sofa cushions, the TOP "
            "of tables and rugs, never their fronts. NO far wall, NO horizon line, NO ceiling anywhere "
            "in frame. It reads like a photographic floor plan."
        )
    if t >= 50:
        return (
            f"STEEP HIGH ANGLE — REPOSITION THE CAMERA. Raise it near the ceiling, around 2.9m, and tilt "
            f"it down {t}° toward the floor. The floor and the top surfaces of the furniture dominate the "
            "frame; only a shallow band of the far wall remains visible at the very top edge."
        )
    if t >= 22:
        return (
            f"HIGH ANGLE — REPOSITION THE CAMERA. Raise it to roughly 2.4m, well above head height, and "
            f"tilt it down {t}° to look into the room from above. The floor and the layout of the "
            "furniture fill the majority of the frame; the horizon line sits high, near the top edge."
        )
    if t >= 8:
        return (
            f"SLIGHTLY ELEVATED — lift the camera to about 1.9m and tilt it down {t}°, a gentle "
            "over-the-shoulder survey of the room. Slightly more floor is visible than at eye level."
        )
    if t > -8:
        return (
            "EYE LEVEL — camera at roughly 1.5-1.6m standing height, lens level with the horizon. "
            "Stable and neutral, the way a person standing in the room actually sees it."
        )
    if t > -22:
        return (
            f"SLIGHTLY LOW — drop the camera to about 1.1m, chest height, tilted up {abs(t)}°. A little "
            "of the ceiling enters the top of the frame and the room starts to feel taller."
        )
    if t > -50:
        return (
            f"LOW ANGLE — REPOSITION THE CAMERA. Drop it to roughly 0.5m, knee height, and tilt it up "
            f"{abs(t)}°. The ceiling becomes clearly visible across the top of the frame, vertical lines "
            "converge upward, and furniture is seen from below its top surfaces so the room towers."
        )
    if t > -78:
        return (
            f"VERY LOW ANGLE — put the camera around 0.25m off the floor tilted up {abs(t)}°. The ceiling "
            "occupies a large part of the frame and the space feels dramatic and imposing."
        )
    return (
        "WORM'S EYE — REPOSITION THE CAMERA COMPLETELY. Place it on the floor itself, only a few "
        "centimetres up, pointing steeply upward. The ceiling dominates the upper half of the frame, "
        "vertical lines converge dramatically toward the top, and the furniture looms overhead seen "
        "from underneath."
    )


def describe_rotation(rotation: int) -> str:
    """Like tilt, this needs the RESULT spelled out. Told only to 'move 90° around
    the room', the model returns the original frontal view — it has to be told
    that the subject is now seen in profile and a different wall is behind it."""
    r = rotation % 360
    if r < 12 or r >= 348:
        return "ORBIT — keep the camera on the same side of the room as the original photograph."

    side = "RIGHT" if r < 180 else "LEFT"
    other = "left" if side == "RIGHT" else "right"
    amount = r if r < 180 else 360 - r

    if amount < 55:
        detail = (
            f"THREE-QUARTER VIEW from the {side.lower()}. The main subject is no longer square-on to the "
            f"camera: you now see its front AND its {side.lower()} side receding away, its front face "
            "angled across the frame. The wall that was straight ahead is now angled, and part of the "
            f"{other} wall enters the frame."
        )
    elif amount < 125:
        detail = (
            f"NEAR-PERPENDICULAR SIDE VIEW from the {side.lower()}. The main subject is seen IN PROFILE — "
            "its side, not its front. What was the back wall of the original is now running away from the "
            f"camera along one edge of the frame, and the {other} wall of the room is now the background "
            "behind the subject. The original camera position is off to one side of this new frame."
        )
    else:
        detail = (
            "REVERSE ANGLE from the opposite side of the room. The camera now stands roughly where the "
            "back wall of the original photo was, looking BACK toward the wall the original was shot "
            "from. The subject is seen from behind, and the background is the part of the room that was "
            "behind the original camera and therefore never visible in the input — invent it plausibly "
            "and consistently with the room's materials."
        )

    return (
        f"ORBIT — REPOSITION THE CAMERA. Walk {amount}° around the room to the {side} and shoot from "
        f"there. Result: {detail} The framing MUST differ clearly from the input; returning the same "
        "frontal composition is wrong. This is a new standing position, not a pan, crop or mirror."
    )


def describe_zoom(zoom: int, focal: int) -> str:
    """Zooming OUT is the hard direction: the model cannot simply crop, it has to
    invent plausible scene beyond the input photo's borders. Say so explicitly —
    without the outpainting instruction the render comes back the same width."""
    # Extension has to be bounded, or the model mirrors the visible half of the
    # room and invents duplicate windows, walls and fixtures — a wide shot of a
    # bathroom came back as a symmetrical hall with two of everything.
    extend_rules = (
        " EXTENSION RULES, follow strictly: continue the existing floor, ceiling and walls outward only; "
        "do NOT mirror or duplicate anything already in frame; do NOT add new windows, doors, rooms, "
        "furniture or fixtures that are not already visible; do NOT make the space symmetrical. Newly "
        "revealed area should be plain continuation — bare floor, bare wall, plain ceiling. The room must "
        "stay the same size and shape it plausibly is, just seen more completely."
    )
    if focal <= 20:
        return (
            f"{focal}mm ULTRA-WIDE — ZOOM OUT HARD. Step the camera well back so the frame takes in "
            "MUCH MORE of the space than the input photograph shows. Everything present in the input must "
            "still appear, but SMALLER and further away, surrounded by newly revealed space. Expansive "
            "wide-angle depth with slight natural barrel character at the edges." + extend_rules
        )
    if focal <= 30:
        return (
            f"{focal}mm WIDE — ZOOM OUT. Pull the camera back so the frame shows noticeably MORE of the "
            "space than the input photograph. Subjects appear smaller and further away." + extend_rules
        )
    if focal <= 45:
        return (
            f"{focal}mm NORMAL — keep the field of view essentially as in the original photograph. "
            "Do not crop in and do not widen out."
        )
    if focal <= 65:
        return (
            f"{focal}mm SHORT TELEPHOTO — ZOOM IN. Crop tighter into the centre of the space so subjects "
            "fill more of the frame, with mildly compressed perspective; background feels closer."
        )
    return (
        f"{focal}mm TELEPHOTO — ZOOM IN HARD. Crop tightly onto the centre of the space so it fills the "
        "frame, with strongly compressed perspective flattening near and far. Much less of the room is "
        "visible than in the input."
    )

# Look & feel: a real photograph of a real place, but with the immersive spatial
# presence of stepping inside it — not a glossy CG architectural render.
STYLE_PROMPT = (
    "STYLE: an immersive, spatially present photograph — as if the viewer is standing inside "
    "the room wearing a VR headset. Wide immersive field of view with a gentle natural spherical "
    "depth to the perspective, strong sense of three-dimensional volume and distance between "
    "foreground and background, deep depth of field so the whole space stays readable. "
    "It must still look like a REAL PHOTOGRAPH taken with a real camera — natural sensor grain, "
    "real-world material texture, honest imperfect surfaces, slightly imperfect natural white "
    "balance. Do NOT make it look like a glossy CGI render, a 3D visualization, an illustration, "
    "or an over-polished hyperreal advertisement. Photographic, not synthetic."
)


def _cache_key(req: FrameSimRequest) -> Tuple[str, int, str, str, str]:
    # Bucket the continuous controls so small scrubs reuse a render instead of
    # firing a new generation for every pixel of drag:
    #   time → the hour, rotation → 15° steps, tilt → 15° steps, zoom → 2 steps.
    hour_bucket = req.time_label.split(":")[0]
    rot_bucket = round((req.rotation % 360) / 15) * 15
    tilt_bucket = round(max(-90, min(90, req.tilt)) / 15) * 15
    zoom_bucket = round(max(1, min(20, req.zoom)) / 2) * 2
    return (
        req.image_url,
        zoom_bucket,
        # The date matters: 17:00 in December is night, in June it is broad daylight.
        f"{req.date_label}@{hour_bucket}",
        f"{req.prompt_version}|{req.image_model}|{req.image_tier}|{req.light_phase}|r{rot_bucket}|t{tilt_bucket}",
        req.render_strategy,
    )



def _crop_for_telephoto(data: bytes, focal: int) -> Tuple[bytes, bool]:
    """Pre-crop the source for long lenses instead of asking the model to crop.

    Measured across 12 listings, every telephoto request came back uncropped —
    the judge scored framing 2.0/5. An editing model is strongly biased toward
    keeping the whole frame, and no wording moved it. Cropping is exact
    arithmetic, so we do it ourselves and let the model spend its effort on the
    thing it is actually good at: relighting and perspective compression.

    Zoom 10 is defined as the photo's own framing, so 35mm is the reference
    focal length and the kept fraction is 35/focal.
    """
    if focal <= 45:
        return data, False
    try:
        from PIL import Image
        import io as _io

        img = Image.open(_io.BytesIO(data))
        frac = max(0.30, 35.0 / focal)
        w, h = img.size
        nw, nh = int(w * frac), int(h * frac)
        left, top = (w - nw) // 2, (h - nh) // 2
        out = _io.BytesIO()
        img.crop((left, top, left + nw, top + nh)).save(out, format="PNG")
        return out.getvalue(), True
    except Exception as e:
        logger.warning(f"telephoto pre-crop failed: {e}")
        return data, False


def _build_prompt_v1(req: "FrameSimRequest", focal: int) -> str:
    """The pre-rewrite wording, kept so evaluation rounds can A/B against it."""
    # 2. Build the relight + reframe prompt from the orbit rig
    kind = scene_kind(req.space_category)
    table = EXTERIOR_PHASE_PROMPTS if kind == "exterior" else PHASE_PROMPTS
    phase_prompt = table.get(req.light_phase, table["afternoon"])
    if kind == "hanok":
        phase_prompt += (
            " This is a traditional Korean hanok: keep the timber frame, paper-screen doors, tiled roof "
            "eaves and courtyard exactly as they are — do not modernise them."
        )
    tilt_prompt = describe_tilt(req.tilt)
    rot_prompt = describe_rotation(req.rotation)
    zoom_prompt = describe_zoom(req.zoom, focal)
    window_note = (
        f" The main windows face {req.window_direction}."
        if req.window_direction and kind != "exterior"
        else ""
    )
    alt = req.sun_altitude_deg
    if alt <= -6:
        sun_note = f"The sun is {abs(alt):.0f}° BELOW the horizon — it has fully set and there is no daylight."
    elif alt <= 0:
        sun_note = "The sun is right at the horizon — twilight, no direct sun."
    elif alt < 15:
        sun_note = f"The sun sits low, only {alt:.0f}° above the horizon, so shadows are very long."
    elif alt < 45:
        sun_note = f"The sun is {alt:.0f}° above the horizon — mid-height, shadows of moderate length."
    else:
        sun_note = f"The sun is high, {alt:.0f}° above the horizon, so shadows are short and steep."

    moved = abs(req.tilt) >= 8 or (req.rotation % 360) not in range(0, 12)
    widening = focal < 30
    change_clause = (
        "WHAT MUST CHANGE — the camera's physical position in the room, the resulting framing, and the "
        "lighting. The camera has MOVED to a new spot on the floor plan; this is a new photograph from a "
        "new tripod position, never a crop, pan or near-copy of the input.\n"
        if moved
        else "WHAT MUST CHANGE — the lens framing and the lighting.\n"
    )
    if widening:
        # Without this the "same proportions" rule above is read as "same framing"
        # and a wide-angle request comes back at the original field of view.
        change_clause += (
            "   The requested lens is WIDER than the input, so the output frame MUST contain more of the "
            "room than the input did. Extending the scene beyond the input image's borders is required "
            "here and is not a violation of keeping the place identical — the architecture stays the "
            "same, you are simply seeing more of it.\n"
        )

    prompt = (
        "You are a cinematography location-scouting previsualization tool. Given this photograph of "
        "a real rental location, produce a NEW photograph of the SAME PLACE, shot from a new camera "
        "rig position, with a different lens, at a different time of day.\n"
        f"1. CAMERA HEIGHT & TILT — {tilt_prompt}\n"
        f"2. CAMERA ORBIT POSITION — {rot_prompt}\n"
        f"3. LENS — {zoom_prompt}\n"
        f"4. TIME & LIGHT — the shot is at {req.time_label} on {req.date_label}. {sun_note}\n"
        f"   {phase_prompt}{window_note}\n"
        "   Light direction, shadow length and colour temperature must be physically consistent with "
        "that sun position. The lighting change must be OBVIOUS at a glance, not a subtle tint.\n"
        "WHAT MUST STAY THE SAME — the identity of the place: the same room, the same wall and "
        "floor materials and colours, the same furniture and decor pieces, the same window and door "
        "placement, the same architectural proportions and ceiling height. A location scout must "
        "recognise it as unmistakably the same venue.\n"
        f"{change_clause}"
        f"{STYLE_PROMPT}\n"
        "Do not add people, text, watermarks, or new objects. Output only the photograph."
    )

    return prompt


async def simulate_frame_with_gemini(req: FrameSimRequest) -> FrameSimResponse:
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    key = _cache_key(req)
    if key in _FRAME_CACHE and not req.bypass_cache:
        return FrameSimResponse(
            image_data_url=_FRAME_CACHE[key],
            cached=True,
            model="cache",
            focal_length_mm=req.focal_length_mm or zoom_to_focal_mm(req.zoom),
            image_tier=req.image_tier,
            prompt_version=(PROMPT_VERSION if req.prompt_version == "v2" else "v1-legacy"),
            render_strategy=req.render_strategy,
        )

    # 1. Fetch the source photo
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(
            req.image_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        resp.raise_for_status()
        image_bytes = resp.content
        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        if not mime.startswith("image/"):
            mime = "image/jpeg"

    # Keep the full source for role-labelled iterative refinement. Camera/lens
    # preprocessing below may replace image_bytes with a telephoto crop.
    source_image_bytes = image_bytes
    source_mime = mime

    # 2. Build the prompt.
    focal = req.focal_length_mm or zoom_to_focal_mm(req.zoom)

    actual_strategy = "standard"
    light_plan = ""
    inv = None
    if req.prompt_version == "v2":
        # The scene reading must happen on the FULL frame — cropping first would
        # hide the openings and anchors that ground the prompt.
        inv = await asyncio.to_thread(get_inventory, req.image_url, image_bytes)
        moved = orbit_amount(req.rotation) >= 12 or abs(req.tilt) >= 8
        lens_changed = focal <= 30 or focal >= 50
        relight_only = not moved and not lens_changed
        source_already_matches = bool(
            inv.input_light_phase and inv.input_light_phase == req.light_phase
        )
        if req.render_strategy != "standard" and relight_only and not source_already_matches:
            actual_strategy = req.render_strategy

        if actual_strategy in {"planned", "iterative"}:
            light_plan = await asyncio.to_thread(
                get_light_plan,
                image_url=req.image_url,
                image_bytes=source_image_bytes,
                mime_type=source_mime,
                light_phase=req.light_phase,
                time_label=req.time_label,
                date_label=req.date_label,
                sun_altitude_deg=req.sun_altitude_deg,
                window_direction=req.window_direction,
            )

        # Long lenses: crop here rather than asking the model to. See
        # _crop_for_telephoto for the measurement that forced this.
        image_bytes, pre_cropped = _crop_for_telephoto(image_bytes, focal)
        if pre_cropped:
            mime = "image/png"
        if actual_strategy in {"standard", "iterative"}:
            prompt = build_prompt(
                rotation=req.rotation,
                tilt=req.tilt,
                zoom=req.zoom,
                focal=focal,
                light_phase=req.light_phase,
                sun_altitude_deg=req.sun_altitude_deg,
                time_label=req.time_label,
                date_label=req.date_label,
                window_direction=req.window_direction,
                inv=inv,
                pre_cropped=pre_cropped,
            )
        else:
            prompt = build_physical_relight_prompt(
                light_phase=req.light_phase,
                sun_altitude_deg=req.sun_altitude_deg,
                time_label=req.time_label,
                date_label=req.date_label,
                window_direction=req.window_direction,
                inv=inv,
                light_plan=light_plan,
            )
    else:
        prompt = _build_prompt_v1(req, focal)
    prompt_fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    # 3. Call Gemini image model
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    last_err: Optional[Exception] = None

    tier = IMAGE_TIERS.get(req.image_tier) or IMAGE_TIERS[DEFAULT_IMAGE_TIER]
    if req.image_model:
        # The eval harness pins one model explicitly; it also pins the size to
        # None so a round measures the model, not a resolution change.
        candidates, image_size = [req.image_model], None
    elif actual_strategy != "standard":
        # Lite is intentionally not used as the first candidate for a
        # multi-reference/high-thinking workflow. It remains a fallback so a
        # temporary model outage degrades to a render rather than a 502.
        candidates = [
            RELIGHT_WORKFLOW_MODEL,
            *[m for m in IMAGE_MODELS if m != RELIGHT_WORKFLOW_MODEL],
        ]
        image_size = tier["image_size"]
    else:
        # The tier's model first, then the generic fallbacks — a tier being
        # unavailable should degrade to a render, not to an error.
        candidates = [tier["model"], *[m for m in IMAGE_MODELS if m != tier["model"]]]
        image_size = tier["image_size"]
    generated_raw: Optional[bytes] = None
    generated_mime = "image/png"
    generated_model = ""

    def generation_config(model_name: str, *, high_thinking: bool):
        kwargs = {}
        if image_size:
            kwargs["image_config"] = types.ImageConfig(image_size=image_size)
        if high_thinking and model_name == RELIGHT_WORKFLOW_MODEL:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="HIGH")
        return types.GenerateContentConfig(**kwargs) if kwargs else None

    for model_name in candidates:
        try:
            # The SDK call is synchronous and takes 30-60s. Called directly from
            # this coroutine it blocks the whole event loop, so one person
            # generating a frame froze every other request — the catalog
            # included. Measured: /api/locations/stats timed out at 30s while
            # six renders were in flight.
            result = await asyncio.to_thread(
                lambda m=model_name: client.models.generate_content(
                    model=m,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime),
                        prompt,
                    ],
                    config=generation_config(
                        m,
                        high_thinking=(actual_strategy in {"physical", "planned"}),
                    ),
                )
            )
            for cand in result.candidates or []:
                for part in cand.content.parts or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        data = inline.data
                        generated_raw = data if isinstance(data, bytes) else base64.b64decode(data)
                        generated_mime = getattr(inline, "mime_type", None) or "image/png"
                        generated_model = model_name
                        break
                if generated_raw is not None:
                    break
            if generated_raw is not None:
                break
            last_err = RuntimeError(f"{model_name} returned no image part")
        except Exception as e:  # try next model name
            last_err = e
            logger.warning(f"Gemini image model {model_name} failed: {e}")

    if generated_raw is None:
        raise RuntimeError(f"GEMINI_IMAGE_GENERATION_FAILED: {last_err}")

    note = ""
    if actual_strategy == "iterative" and inv is not None:
        refinement_prompt = build_relight_refinement_prompt(
            light_phase=req.light_phase,
            sun_altitude_deg=req.sun_altitude_deg,
            time_label=req.time_label,
            date_label=req.date_label,
            window_direction=req.window_direction,
            inv=inv,
            light_plan=light_plan,
        )
        try:
            draft_raw = generated_raw
            draft_mime = generated_mime
            refined = await asyncio.to_thread(
                lambda: client.models.generate_content(
                    model=RELIGHT_WORKFLOW_MODEL,
                    contents=[
                        "REFERENCE A — authoritative place identity and fixed composition:",
                        types.Part.from_bytes(data=source_image_bytes, mime_type=source_mime),
                        "DRAFT B — first lighting attempt to inspect and repair:",
                        types.Part.from_bytes(data=draft_raw, mime_type=draft_mime),
                        refinement_prompt,
                    ],
                    config=generation_config(RELIGHT_WORKFLOW_MODEL, high_thinking=True),
                )
            )
            replacement = None
            replacement_mime = "image/png"
            for cand in refined.candidates or []:
                for part in cand.content.parts or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        data = inline.data
                        replacement = data if isinstance(data, bytes) else base64.b64decode(data)
                        replacement_mime = getattr(inline, "mime_type", None) or "image/png"
                        break
                if replacement is not None:
                    break
            if replacement is not None:
                generated_raw = replacement
                generated_mime = replacement_mime
                generated_model = RELIGHT_WORKFLOW_MODEL
                note = "standard first pass followed by role-labelled lighting refinement"
            else:
                note = "refinement returned no image; standard first pass used"
                actual_strategy = "standard"
        except Exception as exc:
            logger.warning("Gemini relight refinement failed: %s", exc)
            note = "refinement unavailable; standard first pass used"
            actual_strategy = "standard"

    data_url = f"data:{generated_mime};base64,{base64.b64encode(generated_raw).decode()}"
    if len(_FRAME_CACHE) >= _CACHE_MAX:
        _FRAME_CACHE.pop(next(iter(_FRAME_CACHE)))
    _FRAME_CACHE[key] = data_url

    moved_check = None
    if orbit_amount(req.rotation) >= 20 or abs(req.tilt) >= 20:
        moved_check = await asyncio.to_thread(
            camera_actually_moved, image_bytes, generated_raw
        )
    return FrameSimResponse(
        image_data_url=data_url,
        model=generated_model,
        note=note,
        focal_length_mm=focal,
        camera_moved=moved_check,
        image_tier=req.image_tier,
        prompt_version=(PROMPT_VERSION if req.prompt_version == "v2" else "v1-legacy"),
        render_strategy=actual_strategy,
        prompt_fingerprint=prompt_fingerprint,
    )
