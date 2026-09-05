import pytest
from app.models.schemas import SceneInput, FloorPlan, Camera, Point2D, Door, Window, LightingZone, DollyPath, Actor
from app.agent.tools.geometry_engine import calculate_optics, check_spatial_collisions
from app.agent.tools.solar_engine import calculate_solar_position
from app.agent.tools.parallel_search import search_location_constraints_with_parallel
from app.agent.workflow import StageSightAgentWorkflow

def test_optics_calculation():
    # Test 35mm on Full Frame
    optics = calculate_optics(
        sensor_format="Full Frame 36x24",
        focal_length_mm=35.0,
        subject_distance_m=2.4,
        desired_framing="Wide two-shot",
        available_room_depth_m=2.5
    )
    # HFOV for 36mm sensor & 35mm lens: 2 * atan(36 / 70) = ~54.4°
    assert 53.0 < optics.horizontal_fov_deg < 56.0
    # Minimum depth needed = (2.4 * 35 / 36) + 1.2 buffer = 2.33 + 1.2 = 3.53m
    assert optics.minimum_camera_depth_m > 3.0
    assert optics.has_clearance_conflict is True
    assert optics.status == "CALCULATED"

def test_solar_calculation():
    solar = calculate_solar_position(
        lat=-33.8688,
        lon=151.2093,
        target_date="2026-09-15",
        target_time="17:30",
        windows=[Window(id="w1", start=Point2D(x=0, y=0), end=Point2D(x=1, y=0), normal_angle_deg=270.0)]
    )
    assert solar.sunset_time.startswith("17:")
    assert solar.direct_sun_opportunity in ["High", "Moderate"]
    assert "CALCULATED" in solar.status
    assert "ESTIMATED" in solar.direct_sun_status

def test_spatial_collision_detection():
    floor_plan = FloorPlan(
        width_m=6.0,
        depth_m=4.5,
        doors=[Door(id="d1", start=Point2D(x=1.0, y=2.0), end=Point2D(x=2.0, y=2.0), is_fire_exit=True, clearance_radius=1.0)],
        camera=Camera(position=Point2D(x=1.2, y=2.2)),
        actors=[Actor(id="a1", name="Actor 1", position=Point2D(x=4.0, y=2.2))],
        dolly_path=DollyPath(waypoints=[Point2D(x=0.5, y=2.0), Point2D(x=2.5, y=2.0)]),
        lighting_zones=[LightingZone(id="lz1", name="Diffusion Silk", position=Point2D(x=1.5, y=2.2))]
    )
    conflicts = check_spatial_collisions(floor_plan)
    assert len(conflicts) >= 1
    # Check that safety or grip conflict exists
    departments = [c.department for c in conflicts]
    assert any("Safety" in d or "Grip" in d or "Director of Photography" in d for d in departments)

@pytest.mark.asyncio
async def test_parallel_search_citations():
    report = await search_location_constraints_with_parallel(
        venue_name="The Old Woolstore Dining Hall",
        address="14 Macquarie Street, Sydney NSW"
    )
    assert len(report.citations) >= 1
    for citation in report.citations:
        assert citation.title
        assert citation.url.startswith("http")
        assert citation.excerpt
        assert citation.verification_status == "VERIFIED"

@pytest.mark.asyncio
async def test_full_agent_workflow():
    workflow = StageSightAgentWorkflow()
    scene = SceneInput()
    brief = await workflow.execute(scene)
    
    assert brief.project_name == scene.project_name
    assert 0 <= brief.overall_feasibility_score <= 100
    assert brief.optics.focal_length_mm == 35.0
    assert len(brief.conflicts) > 0
    assert len(brief.alternatives) > 0
    assert len(brief.location_research.citations) > 0
