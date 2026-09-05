"""
Prompt construction for the frame simulator (v2).

Everything here is shaped by measurements from evaluation/run_eval.py rather
than taste. The changes that matter, and what they fix:

1. NO IMAGE-PROPERTY WORDS IN THE IDENTITY BLOCK. v1 asked to keep "the same
   architectural proportions", which reads as "keep the composition" — Google's
   own editing template uses that phrasing precisely to make the model hold the
   camera angle. That single clause was fighting every orbit request. Identity is
   now stated only as physical nouns about the PLACE.

2. CAPTION, NOT COMMAND. The prompt opens by asserting the output already exists
   ("This is a photograph of …, taken from a different position in the room") and
   describes what is in that frame, instead of ordering a transformation. This is
   the same mechanism that made tilt work once it described frame content rather
   than an angle.

3. CHANGE FIRST, IDENTITY SECOND, PROHIBITIONS LAST, as one consolidated block of
   concrete visual defects rather than negations scattered through the prose.

4. GROUNDED SYNTHESIS. Newly revealed surfaces are named with the room's real
   materials, taken from scene_inventory, so "invent the unseen part" stops being
   an abstraction the model cannot act on.

5. CONFLICT RESOLUTION IS EXPLICIT. Large orbits and wide lenses both require
   drawing surfaces the source never showed. The prompt says outright that doing
   so is required and is not a violation of keeping the place identical.
"""
from __future__ import annotations

from typing import List, Optional

from app.agent.tools.scene_inventory import SceneInventory

# Bumped on every prompt edit; part of the render cache key so a warm process
# cannot keep serving pre-rewrite images after a deploy.
PROMPT_VERSION = "v12-2026-09-03"

# Stated for any non-trivial camera move. Dropping it in the v2 rewrite cost
# worm -0.40 (camera 2.3/5), low -0.17 and orbit45 -0.20: describing the
# resulting frame is necessary but not sufficient, the model also has to be told
# the tripod physically moved. Deliberately silent at eye level and zero orbit,
# where a false relocation claim would only invite drift.
RELOCATED = (
    "  The camera is not where it was in the reference photograph. This is a second setup, "
    "physically moved, not the same frame re-lit or re-cropped."
)


def zoom_to_focal_mm(zoom: int) -> int:
    z = max(1, min(20, zoom))
    return int(round(16 * (85 / 16) ** ((z - 1) / 19)))


def _norm_rot(rotation: int) -> int:
    return ((rotation % 360) + 360) % 360


def orbit_amount(rotation: int) -> int:
    """Shortest way round, so 350° is a 10° nudge and not a 350° trek.
    v1 used `rotation not in range(0, 12)`, which mislabelled 348-359°."""
    r = _norm_rot(rotation)
    return min(r, 360 - r)


# ── Camera position ─────────────────────────────────────────────────────────
def describe_position(rotation: int, inv: SceneInventory) -> str:
    r = _norm_rot(rotation)
    amount = orbit_amount(rotation)
    if amount < 12:
        return (
            "STANDING POSITION: the photographer is standing where the reference photograph was taken from. "
            "The same side of the space is behind the camera."
        )

    side = "right" if r < 180 else "left"
    other = "left" if side == "right" else "right"
    behind = inv.behind_camera or "the wall the reference photograph was taken from"

    if amount < 55:
        # v8 tried to fix this cell by naming a destination ("a quarter of the
        # way around the space"), holding the subject distance fixed, and
        # forbidding a dolly. Measured on the same 12 cells it did not work:
        # the judge moved +0.08 on camera (2.33 → 2.42, noise at n=12, where an
        # untouched case swung -0.58), while the model-free check went the other
        # way — renders explained by a single homography, i.e. a pan or crop
        # rather than a camera that travelled, rose from 4/12 to 6/11 and the
        # median inlier ratio from 0.64 to 0.70. Two measures disagreeing with
        # the objective one negative is not an improvement, so the v7 wording
        # stands. 45° orbit remains the known weak cell; app/viewpoint_check.py
        # is what tells the user when the angle was not achieved.
        body = (
            f"the photographer has moved a few steps to the {side} and turned back toward the subject. "
            f"Consequences visible in this frame:\n"
            f"  - the main subject is no longer square-on; its front and its {side} side are both visible, "
            "angled across the frame\n"
            f"  - the wall that was straight ahead now runs away from the camera at an angle\n"
            f"  - a slice of the {other} wall has come into frame"
        )
    elif amount < 125:
        body = (
            f"the photographer has crossed to the {side}-hand side of the space and is shooting across it. "
            f"Consequences visible in this frame:\n"
            f"  - the main subject is seen end-on, from its {side} side, not its front\n"
            f"  - the wall that was straight ahead in the reference now runs away along the {side} edge of "
            "the frame, strongly foreshortened\n"
            f"  - {behind} occupies a large part of this frame and is the main background\n"
            "  - a near corner of the space is close to the lens, its vertical edge running down one side"
        )
    else:
        body = (
            "the photographer has walked to the far side of the space and turned around. This is the reverse "
            "angle. Consequences visible in this frame:\n"
            f"  - the camera now stands roughly where the back of the reference photograph was\n"
            f"  - {behind} fills most of the frame — this is the part of the space the reference photograph "
            "never showed\n"
            "  - the subject is seen from behind or from its far side"
        )

    permission = RELOCATED + "\n" if amount >= 20 else ""
    if amount >= 45:
        permission += (
            f"  A large part of this frame shows surfaces the reference photograph never captured. Drawing "
            f"them is required here and does not conflict with this being the same place — the building is "
            f"unchanged, the camera is simply pointed at a different part of it. Build them from this space's "
            f"own materials: {inv.materials_phrase()}."
        )

    tight = ""
    if inv.tightness == "tight" and amount >= 60:
        tight = (
            f"\n  This is a small space, so from here the photographer's back is nearly against the {side} "
            f"wall, and that near wall's corner is close to the lens."
        )

    return f"STANDING POSITION: {body}{permission}{tight}"


# ── Camera height and tilt ──────────────────────────────────────────────────
def describe_height(tilt: int, inv: SceneInventory) -> str:
    """Heights are given as things in the room, not metres — a 2.4 m camera means
    nothing in a room whose ceiling the model cannot measure."""
    t = max(-90, min(90, tilt))
    ceiling_high = inv.ceiling_class in ("tall", "standard", "unknown")

    if t >= 78:
        return (
            "CAMERA HEIGHT: the camera is at the ceiling looking straight down. The floor fills the entire "
            "frame. Every object is seen from directly overhead — the tops of surfaces, never their fronts. "
            "No far wall, no horizon and no ceiling appear anywhere in this frame; it reads like a floor plan."
            + "\n" + RELOCATED
        )
    if t >= 50:
        return (
            f"CAMERA HEIGHT: the camera is close to the ceiling{' , above the top of any door frame' if ceiling_high else ''}, "
            f"tilted {t}° down toward the floor. Floor and the top surfaces of the furniture dominate; only a "
            "shallow band of the far wall remains at the very top of the frame."
            + "\n" + RELOCATED
        )
    if t >= 22:
        return (
            f"CAMERA HEIGHT: the camera is well above head height, tilted {t}° down into the space. The floor "
            "and the layout of the furniture fill most of the frame, and the line where the far wall meets "
            "the floor sits high, near the top edge."
            + "\n" + RELOCATED
        )
    if t >= 8:
        return (
            f"CAMERA HEIGHT: the camera is a little above head height, tilted {t}° down. Slightly more floor "
            "is visible than at eye level."
        )
    if t > -8:
        return (
            "CAMERA HEIGHT: the camera is at the eye level of a standing person, lens level with the horizon."
        )
    if t > -22:
        return (
            f"CAMERA HEIGHT: the camera is at about chest height, tilted {abs(t)}° up. A little of the "
            "ceiling enters the top of the frame."
            + "\n" + RELOCATED
        )
    if t > -50:
        return (
            f"CAMERA HEIGHT: the camera is at about knee height, tilted {abs(t)}° up. The ceiling is clearly "
            "visible across the top of the frame, vertical lines lean together toward the top, and furniture "
            "is seen from below its top surfaces."
            + "\n" + RELOCATED
        )
    if t > -78:
        return (
            f"CAMERA HEIGHT: the camera is just above the floor, tilted {abs(t)}° up. The ceiling takes up "
            "much of the frame and the space looms over the viewer."
            + "\n" + RELOCATED
        )
    return (
        "CAMERA HEIGHT: the camera is resting on the floor pointing steeply upward. The ceiling dominates the "
        "upper half of the frame, vertical lines converge hard toward the top, and furniture towers overhead. "
        "Almost none of the floor is visible.\n" + RELOCATED
    )


# ── Lens ────────────────────────────────────────────────────────────────────
def describe_lens(zoom: int, focal: int, inv: SceneInventory, pre_cropped: bool = False) -> str:
    if pre_cropped:
        # The reference image handed to the model is already the telephoto crop,
        # so asking for a crop again would double-apply it.
        return (
            f"LENS: {focal}mm telephoto. The reference image is already framed as this lens sees it — keep "
            "this framing. Render it with telephoto character: near and far compressed together, a shallower "
            "sense of depth, and the sharpness and detail of a full-resolution photograph rather than an "
            "enlargement."
        )
    if focal <= 20:
        return (
            f"LENS: {focal}mm ultra-wide. This frame takes in much more of the space than the reference "
            "photograph did — more floor toward the viewer, more ceiling, and more wall to each side. "
            "Everything that was in the reference is still here, smaller and further away, surrounded by "
            f"space the reference did not show. That newly visible area is plain continuation of "
            f"{inv.materials_phrase()} — bare surfaces, nothing added. Drawing it is required here and does "
            "not conflict with this being the same place."
        )
    if focal <= 30:
        return (
            f"LENS: {focal}mm wide. This frame shows noticeably more of the space than the reference "
            f"photograph. The extra area at the edges is plain continuation of {inv.materials_phrase()}."
        )
    if focal <= 45:
        return f"LENS: {focal}mm normal. This frame covers about as much of the space as the reference photograph."
    if focal <= 65:
        return (
            f"LENS: {focal}mm short telephoto. This frame is tighter than the reference photograph — the "
            "subject fills more of it, and near and far are compressed together."
        )
    return (
        f"LENS: {focal}mm telephoto. This frame is much tighter than the reference photograph, cropped in on "
        "the centre of the space, with near and far strongly compressed. Far less of the space is visible."
    )


# ── Light ───────────────────────────────────────────────────────────────────
# Measured over four rounds: v1's emphatic wording scored 3.88 on the light
# axis, and every softened rewrite came in lower (3.51, 3.30, 3.51 after
# restoring the intensity words). The research advice to cut ALL-CAPS held for
# identity and geometry but not here, so light keeps v1's table verbatim while
# the rest of the prompt keeps the rewrite. Best of each, not a clean sweep.
INTERIOR_LIGHT = {
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

EXTERIOR_LIGHT = {
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


# v10 tried naming this room's own openings and fixtures as the light sources,
# because a third of pure-relight rows scored light <= 3 and the judge kept
# saying the same thing — "a simple exposure drop", "functions like a color
# grade", "a global cool tint rather than plausible window illumination". The
# prompt forbade a tint but never said what should replace it, so the idea was
# to give the model something concrete to re-light FROM.
#
# Measured on the same 48 cells it did nothing: light 3.62 -> 3.62 exactly. The
# targeted defect moved 12% -> 10% and light<=3 38% -> 33%, both inside noise,
# while realism fell 3.77 -> 3.62 and overall -0.024. Reverted.
#
# Five separate attempts have now failed to move this axis (v4 intensity words,
# v5 the v1 table verbatim, v6 sameness structure, v7 old-sun shadows, v10 named
# sources). Together with the standing disagreement — v7's golden renders
# measure warmer than v1's and its night renders nearly as dark, yet score lower
# — the likeliest remaining explanation is that part of this gap is a judging
# artefact rather than a defect in the render. Anyone continuing should test the
# judge before rewriting the prompt again.


def describe_light(phase: str, sun_alt: float, inv: SceneInventory,
                   window_direction: str, time_label: str, date_label: str) -> str:
    exterior = inv.space_kind == "exterior"
    table = EXTERIOR_LIGHT if exterior else INTERIOR_LIGHT
    body = table.get(phase, table["afternoon"])

    # A source photo already in the requested phase needs the light left alone,
    # not "changed" — otherwise a correct no-op looks like a failure.
    if inv.input_light_phase and inv.input_light_phase == phase:
        return (
            f"LIGHT: the time is {time_label} on {date_label}. The reference photograph was already taken at "
            "this time of day, so the light is already right — carry it over unchanged and let only the "
            "camera differ."
        )

    sun = (
        f"The sun is {abs(sun_alt):.0f}° below the horizon and has set."
        if sun_alt <= -6 else
        "The sun is right at the horizon." if sun_alt <= 0 else
        f"The sun is low, {sun_alt:.0f}° above the horizon, so shadows are long."
        if sun_alt < 15 else
        f"The sun is {sun_alt:.0f}° above the horizon." if sun_alt < 45 else
        f"The sun is high, {sun_alt:.0f}° up, so shadows are short and steep."
    )

    extra = ""
    if not exterior and not inv.has_openings and phase in ("night", "blue_hour", "golden_hour"):
        # A windowless studio must not sprout a window to justify the hour.
        extra = (
            " This space has no window and no view outdoors, so the time of day changes only which fittings "
            "are lit and how their light pools. Do not add a window, a doorway to outside, or any view of sky."
        )
    elif not exterior and any(d in window_direction for d in ("남", "북", "동", "서")) \
            and "암막" not in window_direction:
        # Only a real bearing is useful; "자연광 (방향 미표기)" says nothing about direction.
        extra = f" The main windows face {window_direction}."

    if phase in ("night", "blue_hour") and not inv.fixtures and not exterior:
        extra += " The light comes from fittings just outside the frame; do not add visible lamps."

    return (
        f"LIGHT: the time is {time_label} on {date_label}. {sun} It is {body}{extra}\n"
        "  The lighting is the first thing a viewer should notice about this photograph. It must be "
        "unmistakable at a glance — a different time of day, not the reference photograph with a colour "
        "tint over it. Shadow direction, shadow length and colour temperature all follow from that sun "
        "position.\n"
        # Measured: the commonest remaining light failure is not a weak change but
        # a global one. The model darkens or tints the whole frame and leaves the
        # original sun's shadows and highlights sitting on the walls — the judge
        # called it "merely darkened; daylight sun shadows remain visible". The
        # frame is physically warmer/darker than v1's yet reads wrong, because
        # two suns are visible at once. Naming the old shadows is the only way to
        # get them removed; describing the new ones is not enough.
        "  Every shadow and every bright patch in the reference photograph was cast by the sun at that "
        "hour, and that sun is no longer there. Those shadows and highlights are gone from this "
        "photograph. Re-light the room from scratch for the hour above rather than tinting or dimming "
        "the reference: no shadow may point the old way, and no sunlit patch may sit where the old sun "
        "put it."
    )


# A prompt-only relighting experiment. Unlike the long general-purpose prompt
# below, this branch is used only when the camera and lens stay fixed. It names
# the components a renderer has to recompute (direct light, ambient fill,
# bounce, specular response and occlusion) instead of describing the desired
# colour palette. Recent relighting systems condition on geometry/material
# intrinsics and target illumination separately; this is the closest useful
# analogue available through a general image-editing API without training a
# dedicated model. It remains opt-in until evaluation justifies shipping it.
PHYSICAL_INTERIOR_LIGHT = {
    "night": (
        "Direct sunlight is zero. Outdoor apertures carry near-black night values. Existing practical "
        "fixtures are the only emitters: each creates a local pool, realistic inverse-distance falloff, "
        "warm bounce on nearby surfaces, matching reflections, and deep occluded zones between pools."
    ),
    "blue_hour": (
        "Direct sunlight is zero. Deep indigo skylight enters diffusely through existing apertures, so "
        "surfaces facing them receive cooler fill than surfaces turned away. Existing practical fixtures "
        "form localized warm pools and reflections, with a spatial warm-cool transition through the room."
    ),
    "golden_hour": (
        "Low orange sunlight enters through existing apertures as a directional source. It produces long "
        "projected shadows, warm highlights on surfaces facing the opening, cool skylight fill on surfaces "
        "turned away, indirect amber bounce, and matching highlights in glass and polished materials."
    ),
    "morning": (
        "Low-to-mid morning daylight enters from existing apertures. Direct light forms clear geometric "
        "patches and medium-length shadows; cool-neutral sky fill reaches the rest of the room; pale bounce "
        "light lifts nearby walls and the floor while corners keep natural occlusion."
    ),
    "midday": (
        "High neutral daylight enters from existing apertures. Direct patches are bright, shadows are short "
        "and comparatively crisp, sky fill is broad and neutral, and reflected light from the floor and "
        "walls illuminates nearby surfaces without flattening their orientation."
    ),
    "afternoon": (
        "Descending warm daylight enters from existing apertures. Slanted direct light produces lengthening "
        "shadows, warm-facing planes, neutral ambient fill, indirect bounce and material-appropriate "
        "reflections across the room."
    ),
}

PHYSICAL_EXTERIOR_LIGHT = {
    "night": (
        "Direct sunlight and daylight are zero. The sky and distant unlit areas are near black. Existing "
        "street, facade and window lights create separate local pools with distance falloff, reflected "
        "light on nearby ground and walls, matching specular highlights, and deep occluded shadow."
    ),
    "blue_hour": (
        "Direct sunlight is zero. Deep indigo sky illumination provides cool directional ambient fill from "
        "above; existing artificial sources create localized warm pools and reflections; surfaces vary by "
        "orientation and distance instead of sharing one blue value."
    ),
    "golden_hour": (
        "Low orange sunlight rakes across the location, producing long cast shadows, warm highlights and rim "
        "light on sun-facing edges, cool skylight fill on turned-away planes, indirect ground bounce, and "
        "material-appropriate reflections."
    ),
    "morning": (
        "Low-to-mid morning sun creates crisp neutral highlights and medium-length directional shadows. Cool "
        "sky fill reaches turned-away planes and natural ground bounce lifts nearby shaded surfaces."
    ),
    "midday": (
        "High neutral sun creates bright top light and short shadows directly below objects. Broad sky fill, "
        "ground bounce and material-specific reflections preserve readable detail without flattening depth."
    ),
    "afternoon": (
        "Descending warm sun creates slanted highlights and lengthening shadows. Neutral sky fill reaches "
        "turned-away planes, with natural ground bounce and material-specific reflections."
    ),
}


def _fixed_scene_facts(inv: SceneInventory) -> str:
    facts = [f"materials: {inv.materials_phrase()}"]
    if inv.openings:
        facts.append("existing apertures: " + "; ".join(inv.openings[:3]))
    if inv.fixtures:
        facts.append("existing practical fixtures: " + "; ".join(inv.fixtures[:4]))
    if inv.anchors:
        facts.append("identity anchors: " + "; ".join(inv.anchors[:6]))
    return "\n".join(f"- {fact}" for fact in facts)


def build_physical_relight_prompt(
    *,
    light_phase: str,
    sun_altitude_deg: float,
    time_label: str,
    date_label: str,
    window_direction: str,
    inv: SceneInventory,
    light_plan: str = "",
) -> str:
    """A situation-specific prompt for a fixed-camera relight.

    It deliberately does not serve camera moves or lens changes: combining
    geometry synthesis with relighting is a different problem and would make an
    A/B result impossible to attribute.
    """
    table = PHYSICAL_EXTERIOR_LIGHT if inv.space_kind == "exterior" else PHYSICAL_INTERIOR_LIGHT
    transport = table.get(light_phase, table["afternoon"])
    sun = (
        f"The sun is {abs(sun_altitude_deg):.0f} degrees below the horizon."
        if sun_altitude_deg <= -6 else
        "The sun is at the horizon."
        if sun_altitude_deg <= 0 else
        f"The sun is {sun_altitude_deg:.0f} degrees above the horizon."
    )
    direction = (
        f" The known aperture bearing is {window_direction}."
        if window_direction and inv.space_kind != "exterior" else ""
    )
    windowless = (
        " This is an enclosed space without a visible outdoor aperture; the requested hour is expressed "
        "through the existing practical fixtures and their spatial falloff."
        if inv.space_kind != "exterior" and not inv.has_openings else ""
    )

    plan_block = (
        "\n\nSCENE-SPECIFIC LIGHT PLAN (derived from the actual reference image):\n"
        f"{light_plan}"
        if light_plan else ""
    )

    return (
        "Create one finished location-scout photograph of the reference place. The tripod, lens, image "
        "boundaries, architecture, objects and any existing occupants stay exactly fixed. Use the reference "
        "as the authoritative record of geometry, material colour and fine texture.\n\n"
        f"TARGET CAPTURE: {time_label} on {date_label}; {light_phase}. {sun}{direction}{windowless}\n"
        f"PHYSICAL LIGHT TRANSPORT: {transport}{plan_block}\n\n"
        "Reconstruct the illumination over the fixed scene. For every visible surface, integrate direct "
        "light, ambient sky or room fill, indirect bounce, contact shadow, cast shadow and specular response "
        "according to its orientation, distance from a source, material and occlusion. Existing light from "
        "the reference is replaced by this target illumination. The finished photograph has locally varying "
        "irradiance, colour, shadow and reflection that reveal source distance and surface orientation.\n\n"
        "FIXED SCENE RECORD:\n"
        f"{_fixed_scene_facts(inv)}\n\n"
        "The result is a natural real-camera photograph with honest surface texture, plausible reflections, "
        "soft sensor grain and readable shadow detail. It contains the same place and entities, with no new "
        "architecture, furnishings, signage or people. Output one photograph only."
    )


def build_relight_refinement_prompt(
    *,
    light_phase: str,
    sun_altitude_deg: float,
    time_label: str,
    date_label: str,
    window_direction: str,
    inv: SceneInventory,
    light_plan: str = "",
) -> str:
    """Repair a first relighting draft using the source as an identity anchor."""
    target = build_physical_relight_prompt(
        light_phase=light_phase,
        sun_altitude_deg=sun_altitude_deg,
        time_label=time_label,
        date_label=date_label,
        window_direction=window_direction,
        inv=inv,
        light_plan=light_plan,
    )
    return (
        "REFERENCE A above is the authoritative source for place identity, geometry, material colours, "
        "objects, occupants, fixed camera and fixed framing. DRAFT B above is a first lighting attempt, not "
        "an authority for geometry.\n\n"
        "Silently inspect DRAFT B for four common defects: uniform exposure or colour grading; remnants of "
        "the reference hour's bright patches and shadows; practical fixtures whose emitted light does not "
        "match their state; and missing indirect bounce, reflections or occlusion. Then synthesize one "
        "corrected photograph. Take all identity and composition from REFERENCE A and retain from DRAFT B "
        "only lighting that is physically consistent with the target.\n\n"
        f"{target}"
    )


# ── Identity ────────────────────────────────────────────────────────────────
def describe_place(inv: SceneInventory, cropped: bool = False, same_camera: bool = False) -> str:
    """Physical nouns only.

    Deliberately says nothing about composition, framing, perspective, viewpoint,
    crop, or proportions of the picture. Naming any of those makes the model hold
    the camera still, which is exactly the bug this rewrite exists to fix.
    """
    lines = ["THE PLACE: both photographs show the same building. Physically unchanged between them:"]
    if inv.floor:
        lines.append(f"  - the floor: {inv.floor}")
    if inv.walls:
        lines.append(f"  - the walls: {inv.walls}")
    if inv.ceiling and inv.space_kind != "exterior":
        lines.append(f"  - the ceiling: {inv.ceiling}")
    if not cropped:
        # A cropped reference may not contain these; naming them invites the
        # model to reinstate objects that are outside the frame.
        for o in inv.openings[:4]:
            lines.append(f"  - {o}")
    anchors = "" if cropped else inv.anchor_lines(6)
    if anchors:
        lines.append("  - these objects, unchanged in shape, colour and material:")
        lines.append(anchors)
    if len(lines) == 1:
        lines.append(
            "  - the same rooms, surfaces, fittings and furniture, in the same materials and colours"
        )
    if cropped:
        lines.append(
            "  Only what is inside the reference frame belongs in this photograph. Do not add objects, "
            "doors, windows or signage that the reference frame does not show."
        )
    if same_camera:
        # On a pure relight the tripod did not move, and claiming it did
        # contradicts the CAMERA line directly above. What this block has to buy
        # here is only that the building is unchanged while its light is not.
        lines.append(
            "  Being the same place is a fact about the building, not about the picture. The building is "
            "identical in both photographs; what the sun is doing to it is not."
        )
    else:
        lines.append(
            "  Being the same place is a fact about the building, not about the picture. These are two "
            "photographs from the same location scout, taken from two different tripod positions. Surfaces the "
            "reference photograph did not happen to show will appear in this one; showing them is expected."
        )
    return "\n".join(lines)


STYLE_NEAR = (
    "LOOK: a real photograph taken on a real camera — true materials, natural surface texture, honest "
    "imperfections, natural light falloff, faint sensor grain."
)
STYLE_WIDE = STYLE_NEAR + " Wide-angle depth, with slight natural barrel character at the edges."

PROHIBITIONS = """DO NOT PRODUCE:
  - a mirrored or duplicated copy of any wall, window, fitting or piece of furniture
  - more windows, doors or rooms than the reference photograph has
  - a symmetrical room
  - people, text, watermarks or logos
  - a CGI, 3D-visualisation or over-polished advertising look"""


def build_prompt(
    *,
    rotation: int,
    tilt: int,
    zoom: int,
    focal: int,
    light_phase: str,
    sun_altitude_deg: float,
    time_label: str,
    date_label: str,
    window_direction: str,
    inv: SceneInventory,
    pre_cropped: bool = False,
) -> str:
    amount = orbit_amount(rotation)
    moved = amount >= 12 or abs(tilt) >= 8
    lens_changed = focal <= 30 or focal >= 50
    # When only the hour changes, the camera sections have nothing to ask for —
    # and measured over five rounds, letting them speak anyway is actively
    # harmful. They open with "from the same camera position", then say the
    # photographer stands where the reference was taken from, at the same
    # height, covering about the same field: four assertions of sameness before
    # the light block is reached. That is a description of the input image, and
    # the model duly returned it — no-ops on pure relights ran 2% under v1's
    # change-first wording and 17% under this one, dragging the light axis from
    # 4.02 to 3.56. Below, a relight states the change first and says the camera
    # is unchanged once, in one line.
    relight_only = not moved and not lens_changed and not pre_cropped

    where = (
        "from a different position in the space"
        if amount >= 12 else
        "from the same spot at a different camera height" if abs(tilt) >= 8 else
        "from the same camera position"
    )
    if relight_only:
        caption = (
            f"This is a second photograph of the same place, taken later in the same day at {time_label}, "
            f"when the light on the location had completely changed. The hour is what is different between "
            f"the two photographs."
        )
    else:
        caption = (
            f"This is a photograph of the same place as the reference image, taken during the same location "
            f"scout, {where}, at {time_label}."
        )

    if relight_only:
        camera_block = [
            "CAMERA: unchanged from the reference photograph — same position, same height, same lens. "
            "Nothing about the framing is being asked for here.",
        ]
    else:
        camera_block = [
            describe_position(rotation, inv),
            describe_height(tilt, inv),
            describe_lens(zoom, focal, inv, pre_cropped),
        ]

    parts = [
        caption,
        "",
        *camera_block,
        "",
        describe_light(light_phase, sun_altitude_deg, inv, window_direction, time_label, date_label)
        + (
            "\n  At this camera angle little of the window may be in frame, so the time of day has to be "
            "carried by the surfaces that are: the colour and direction of the light spilling across the "
            "ceiling, walls and floor must still say what hour it is."
            if abs(tilt) >= 50 else ""
        ),
        "",
        describe_place(inv, cropped=pre_cropped, same_camera=relight_only),
        "",
        STYLE_WIDE if focal <= 30 else STYLE_NEAR,
        "",
        PROHIBITIONS,
    ]

    if pre_cropped:
        parts.append(
            "\nThe reference frame is already the finished framing. Do not widen it, do not zoom out, and do "
            "not reveal anything beyond its edges."
        )

    if moved or lens_changed:
        parts.append(
            "\nAgain: this is a new photograph from a new camera setup. It is not the reference photograph "
            "with the colours adjusted."
        )
    elif relight_only:
        # v1 closed a relight by naming the change ("WHAT MUST CHANGE — the
        # lighting") and scored 4.02 on these cases. The rewrite dropped the
        # line and every round since has come in between 3.56 and 3.75.
        parts.append(
            "\nWhat is different between the two photographs is the light, and it must be different enough "
            "that the hour is obvious without being told. Returning the reference photograph unchanged, or "
            "with a faint tint over it, is a failure."
        )

    parts.append("\nOutput a single photograph and nothing else.")
    return "\n".join(parts)
