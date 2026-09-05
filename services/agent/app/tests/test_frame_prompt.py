"""Locks in the prompt behaviours that measurement showed were load-bearing.

Each test here corresponds to a failure observed in evaluation/results and the
fix that moved the score. They are cheap string assertions, but they stop a
future tidy-up from silently reintroducing a regression that costs a 40-minute
eval round to rediscover.
"""
import pytest

from app.agent.tools.frame_prompt import (
    build_prompt,
    build_physical_relight_prompt,
    build_relight_refinement_prompt,
    describe_place,
    describe_lens,
    orbit_amount,
)
from app.agent.tools.light_planner import format_light_plan
from app.agent.tools.scene_inventory import SceneInventory


def _inv(**over):
    base = dict(
        space_kind="interior",
        tightness="normal",
        ceiling_class="standard",
        floor="pale oak planks",
        walls="white plaster",
        ceiling="white plaster",
        openings=["a tall window in the left wall"],
        fixtures=["a black pendant lamp"],
        anchors=["a freestanding white bathtub", "two tall potted plants"],
        input_light_phase="midday",
        behind_camera="a plain white wall with a doorway",
        ok=True,
    )
    base.update(over)
    return SceneInventory(**base)


def _prompt(**over):
    kw = dict(
        rotation=0, tilt=0, zoom=10, focal=35,
        light_phase="midday", sun_altitude_deg=60.0,
        time_label="14:00", date_label="2026-09-01",
        window_direction="남서향 (220°)", inv=_inv(),
    )
    kw.update(over)
    return build_prompt(**kw)


# ── The clause that was suppressing every orbit ─────────────────────────────
BANNED = ["composition", "framing of the image", "perspective of the photo",
          "camera angle", "viewpoint", "same proportions", "everything else"]


def test_identity_block_never_names_an_image_property():
    """Asking to preserve "proportions"/"composition" reads as "hold the camera
    still" and was fighting every orbit request."""
    text = describe_place(_inv()).lower()
    for word in BANNED:
        assert word not in text, f"identity block must not mention {word!r}"


def test_identity_block_names_physical_materials_instead():
    text = describe_place(_inv())
    assert "pale oak planks" in text
    assert "white plaster" in text
    assert "freestanding white bathtub" in text


# ── Orbit ───────────────────────────────────────────────────────────────────
def test_orbit_amount_takes_the_short_way_round():
    """v1 used `rotation not in range(0, 12)`, so 350° was treated as a 350°
    trek rather than a 10° nudge."""
    assert orbit_amount(0) == 0
    assert orbit_amount(350) == 10
    assert orbit_amount(90) == 90
    assert orbit_amount(270) == 90
    assert orbit_amount(180) == 180


def test_large_orbit_grants_permission_to_synthesise():
    p = _prompt(rotation=90)
    assert "required here" in p
    assert "pale oak planks" in p  # synthesis grounded in the room's own materials


def test_small_orbit_does_not_grant_permission():
    assert "required here and does not conflict" not in _prompt(rotation=20)


def test_tight_space_orbit_says_the_photographer_is_against_a_wall():
    p = _prompt(rotation=90, inv=_inv(tightness="tight"))
    assert "small space" in p


def test_zero_rotation_states_the_camera_stayed_put():
    """Still true whenever anything else about the camera is being asked for —
    here a tilt, so the orbit block still has to say the photographer did not
    also walk. A pure relight is the exception, covered below."""
    assert "standing where the reference photograph was taken from" in _prompt(rotation=0, tilt=35)


# ── Pure relight: change first, sameness once ───────────────────────────────
def test_relight_only_does_not_repeat_sameness_before_the_light_block():
    """Measured across five rounds. With the camera sections speaking on a pure
    relight, the prompt asserted sameness four times (caption "from the same
    camera position", STANDING POSITION, CAMERA HEIGHT, LENS) before reaching
    the light. That is a description of the input, and the model returned it:
    no-ops on pure relights went 2% under v1 to 17%, and light 4.02 to 3.56."""
    p = _prompt(rotation=0, tilt=0, zoom=10, focal=35)
    head = p[: p.index("LIGHT:")]
    assert "STANDING POSITION" not in head
    assert "CAMERA HEIGHT" not in head
    assert "from the same camera position" not in head
    # Sameness is stated once, plainly.
    assert head.count("unchanged from the reference photograph") == 1


def test_relight_only_opens_by_asserting_the_light_changed():
    p = _prompt(rotation=0, tilt=0)
    assert p.startswith("This is a second photograph of the same place")
    assert "the light on the location had completely changed" in p


def test_relight_only_closes_by_naming_the_change():
    """v1 closed with "WHAT MUST CHANGE — the lighting" and scored 4.02; the
    rewrite dropped it and every round since landed between 3.56 and 3.75."""
    p = _prompt(rotation=0, tilt=0)
    assert "Returning the reference photograph unchanged" in p


def test_relight_only_does_not_claim_the_tripod_moved():
    """THE PLACE otherwise says the two photographs come from "two different
    tripod positions", which contradicts the CAMERA line directly above it."""
    p = _prompt(rotation=0, tilt=0)
    assert "two different tripod positions" not in p
    assert "what the sun is doing to it is not" in p
    # A real move must still get the original wording.
    assert "two different tripod positions" in _prompt(rotation=90)


def test_a_camera_move_still_gets_the_full_position_description():
    """The relight-only shortcut must not touch the path the rewrite improved."""
    for kw in (dict(rotation=45), dict(tilt=35), dict(zoom=1, focal=16), dict(zoom=20, focal=85)):
        p = _prompt(**kw)
        assert not p.startswith("This is a second photograph"), f"{kw} took the relight path"


# ── Lens ────────────────────────────────────────────────────────────────────
def test_telephoto_with_a_pre_cropped_source_must_not_ask_for_another_crop():
    """The source is cropped before it is sent; asking to crop again double-applies."""
    text = describe_lens(20, 85, _inv(), pre_cropped=True)
    assert "already framed as this lens sees it" in text
    assert "tighter than the reference" not in text


def test_pre_cropped_prompt_forbids_widening_back_out():
    """Measured: the model outpainted the crop back to a full room view."""
    p = _prompt(zoom=20, focal=85, pre_cropped=True)
    assert "Do not widen" in p


def test_pre_cropped_prompt_omits_anchors_outside_the_crop():
    """Naming anchors the crop excluded made the model paint them back in."""
    p = _prompt(zoom=20, focal=85, pre_cropped=True)
    assert "freestanding white bathtub" not in p
    assert "tall window in the left wall" not in p


def test_wide_lens_asks_for_more_of_the_space_and_bounds_the_invention():
    p = _prompt(zoom=1, focal=16)
    assert "much more of the space" in p
    assert "mirrored or duplicated" in p  # the prohibition that fixed the mirrored room


def test_wide_style_only_applies_to_wide_lenses():
    """"Wide immersive field of view" in the shared style block was fighting
    every telephoto request; framing scored 2.0/5 until it became conditional."""
    assert "Wide-angle depth" in _prompt(zoom=1, focal=16)
    assert "Wide-angle depth" not in _prompt(zoom=20, focal=85)


# ── Light ───────────────────────────────────────────────────────────────────
def test_windowless_space_is_told_not_to_grow_a_window_at_night():
    p = _prompt(light_phase="night", sun_altitude_deg=-28.0, inv=_inv(openings=[]))
    assert "no window and no view outdoors" in p
    assert "Do not add a window" in p


def test_exterior_night_talks_about_sky_not_windows():
    p = _prompt(light_phase="night", sun_altitude_deg=-28.0,
                inv=_inv(space_kind="exterior", openings=[]))
    assert "sky is black" in p.lower()
    assert "Beyond every window" not in p


def test_relight_says_the_old_suns_shadows_are_gone():
    """The commonest remaining light failure was a global one: the model darkened
    or tinted the frame and left the original sun's shadows on the walls, so two
    suns were visible at once. Model-free metrics said those renders were warmer
    and darker than v1's while the judge scored them lower — describing the new
    light is not enough, the old shadows have to be named to be removed."""
    p = _prompt(light_phase="night", sun_altitude_deg=-28.0)
    assert "that sun is no longer there" in p
    assert "no shadow may point the old way" in p


def test_a_no_op_relight_is_not_told_to_erase_shadows():
    """When the source is already in the requested phase its shadows are correct;
    telling the model they are gone would break a render that is already right."""
    p = _prompt(light_phase="midday", inv=_inv(input_light_phase="midday"))
    assert "that sun is no longer there" not in p


def test_source_already_in_the_requested_phase_keeps_its_light():
    """A correct no-op relight was being scored as a failure to change."""
    p = _prompt(light_phase="midday", inv=_inv(input_light_phase="midday"))
    assert "already right" in p


def test_sun_below_horizon_is_stated_as_set():
    assert "has set" in _prompt(light_phase="night", sun_altitude_deg=-28.0)


# ── Structure ───────────────────────────────────────────────────────────────
def test_prompt_opens_as_a_caption_not_a_command():
    assert _prompt(rotation=90).startswith("This is a photograph of the same place")


def test_prohibitions_are_one_block_at_the_end():
    p = _prompt(rotation=90)
    assert p.index("DO NOT PRODUCE:") > p.index("THE PLACE:")
    assert p.rstrip().endswith("Output a single photograph and nothing else.")


def test_a_moved_camera_gets_a_closing_restatement():
    assert "not the reference photograph with the colours adjusted" in _prompt(rotation=90)


def test_missing_inventory_degrades_to_generic_wording_without_crashing():
    """The inventory call is best-effort; a failure must not break rendering."""
    p = build_prompt(
        rotation=45, tilt=30, zoom=5, focal=22, light_phase="golden_hour",
        sun_altitude_deg=5.0, time_label="18:30", date_label="2026-09-01",
        window_direction="", inv=SceneInventory(),
    )
    assert "STANDING POSITION" in p and "THE PLACE" in p
    assert len(p) > 500


def test_light_block_states_the_change_must_be_obvious():
    """Softening this line in v2 cost 0.05 mean light score, with golden, blue
    and morning all falling to 3.4 — the emphasis is load-bearing."""
    p = _prompt(light_phase="golden_hour", sun_altitude_deg=5.0)
    assert "unmistakable at a glance" in p
    assert "colour tint" in p


@pytest.mark.asyncio
async def test_image_generation_does_not_block_the_event_loop():
    """The Gemini SDK call is synchronous and takes 30-60s. Awaited directly it
    froze every other request — /api/locations/stats timed out at 30s while six
    renders were in flight. It must run in a thread."""
    import asyncio
    import inspect

    from app.agent.tools import frame_simulator

    src = inspect.getsource(frame_simulator.simulate_frame_with_gemini)
    assert "asyncio.to_thread" in src, "the blocking SDK call must be offloaded to a thread"
    # The bare synchronous form must not reappear.
    assert "result = client.models.generate_content(" not in src


def test_any_real_camera_move_states_that_the_tripod_moved():
    """Dropping this in v2 cost worm -0.40 (camera 2.3), low -0.17, orbit45 -0.20.
    Describing the resulting frame is necessary but not sufficient — the model
    also has to be told the camera physically relocated."""
    for kw in (dict(tilt=-80), dict(tilt=-35), dict(tilt=35), dict(tilt=90), dict(rotation=20)):
        assert "not where it was" in _prompt(**kw), f"missing relocation statement for {kw}"


def test_a_still_camera_is_not_told_it_moved():
    """baseline must stay put; a false relocation claim would invite drift."""
    assert "not where it was" not in _prompt(rotation=0, tilt=0)


def test_steep_angles_ask_for_the_time_of_day_to_stay_readable():
    """Pointed at the ceiling there is often no window in frame, and light scored 2.5."""
    assert "carried by the surfaces" in _prompt(tilt=-80)
    assert "carried by the surfaces" not in _prompt(tilt=0)


def test_viewpoint_check_calls_a_static_render_unmoved():
    """A judge scored 6/10 nominal 90-degree orbits at 1.7/5 without being able
    to say why; this is the measurement that can."""
    from pathlib import Path

    from app.viewpoint_check import camera_actually_moved, homography_inlier_ratio

    p = Path(__file__).resolve().parents[2] / "evaluation/results/images/r1/hp_62013_baseline.png"
    if not p.exists():
        pytest.skip("no eval renders on disk")
    same = p.read_bytes()
    assert homography_inlier_ratio(same, same) == 1.0
    assert camera_actually_moved(same, same) is False


def test_viewpoint_check_is_honest_about_not_knowing():
    """Too few keypoints must return None, never a confident answer."""
    import io

    from PIL import Image

    from app.viewpoint_check import camera_actually_moved

    blank = io.BytesIO()
    Image.new("RGB", (256, 256), (128, 128, 128)).save(blank, format="PNG")
    b = blank.getvalue()
    assert camera_actually_moved(b, b) is None


def test_light_phases_keep_their_intensity_statement():
    """Four rounds measured the light axis at 3.88 with v1's emphatic wording and
    3.51 / 3.30 / 3.51 with every softened rewrite. The advice to cut ALL-CAPS
    held for identity and geometry but not here, so light keeps v1's table."""
    from app.agent.tools.frame_prompt import INTERIOR_LIGHT, EXTERIOR_LIGHT

    for table in (INTERIOR_LIGHT, EXTERIOR_LIGHT):
        assert "FULL NIGHT" in table["night"]
        assert "read instantly as a night photograph" in table["night"]
        assert "GOLDEN HOUR" in table["golden_hour"]
        assert "LONG" in table["golden_hour"]
    # Interior talks about windows; exterior talks about sky. Mixing them up made
    # outdoor night renders grow interior walls.
    assert "window" in INTERIOR_LIGHT["night"]
    assert "outdoors" in EXTERIOR_LIGHT["night"]


def test_45_degree_orbit_keeps_the_wording_that_measured_best():
    """v8 rewrote this branch to name a destination and forbid a dolly. On the
    same 12 cells the judge moved +0.08 (noise) while the model-free check got
    worse — static/pan renders 4/12 → 6/11. Reverted; this test stops the
    rewrite being reapplied without a round that justifies it."""
    p = _prompt(rotation=45)
    assert "moved a few steps to the" in p
    assert "a quarter of the way around the space" not in p


def test_the_90_degree_branch_is_untouched():
    """It already outscored 45°, so the fix must not spill into it."""
    p = _prompt(rotation=90)
    assert "crossed to the" in p
    assert "a quarter of the way around" not in p


def test_named_light_sources_stay_out_of_the_prompt():
    """v10 named the room's openings and fixtures as the light sources. On the
    same 48 cells light moved 3.62 -> 3.62 exactly, realism fell 0.15, and the
    defect it targeted moved only 12% -> 10%. Reverted; this stops it being
    reapplied without a round that justifies it."""
    p = _prompt(light_phase="night", sun_altitude_deg=-28.0)
    assert "re-lighting, not a grade" not in p


def test_physical_relight_separates_light_transport_components():
    p = build_physical_relight_prompt(
        light_phase="blue_hour",
        sun_altitude_deg=-4.0,
        time_label="19:30",
        date_label="2026-09-01",
        window_direction="남서향 (220°)",
        inv=_inv(),
        light_plan="- target emitters: cool sky at frame-left; warm pendant at centre",
    )
    for concept in ("direct light", "ambient", "indirect bounce", "cast shadow", "specular"):
        assert concept in p.lower()
    assert "SCENE-SPECIFIC LIGHT PLAN" in p
    assert "cool sky at frame-left" in p
    assert "same place and entities" in p


def test_relight_refinement_assigns_authority_to_each_reference():
    p = build_relight_refinement_prompt(
        light_phase="night",
        sun_altitude_deg=-28.0,
        time_label="22:00",
        date_label="2026-09-01",
        window_direction="",
        inv=_inv(),
    )
    assert "REFERENCE A" in p and "authoritative" in p
    assert "DRAFT B" in p and "first lighting attempt" in p
    assert "Take all identity and composition from REFERENCE A" in p


def test_light_plan_formatter_keeps_roles_explicit():
    p = format_light_plan({
        "target_emitters": "window at frame-left",
        "direct_receivers": "rear wall",
        "ambient_fill": "cool fill from frame-left",
        "fixture_behavior": "pendant on",
        "cast_shadows": "table shadow extends frame-right",
        "indirect_and_reflections": "warm floor bounce",
        "reference_cues_to_replace": "old right-facing shadow",
        "continuity_risks": "glass reflections",
    })
    assert "target emitters: window at frame-left" in p
    assert "reference-hour cues to replace: old right-facing shadow" in p
