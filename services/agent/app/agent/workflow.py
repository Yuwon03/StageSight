import logging
from datetime import datetime
from typing import Dict, Any

from app.models.schemas import SceneInput, SpatialProductionBrief
from app.agent.tools.scene_analyzer import analyze_scene_intent
from app.agent.tools.geometry_engine import calculate_optics, dist_point_to_point, Point2D
from app.agent.tools.solar_engine import calculate_solar_position
from app.agent.tools.parallel_search import search_location_constraints_with_parallel
from app.agent.tools.conflict_detector import detect_department_conflicts, calculate_feasibility_score
from app.agent.tools.alternatives_engine import generate_production_alternatives

logger = logging.getLogger(__name__)

class StageSightAgentWorkflow:
    """
    Python multi-step workflow orchestrator for StageSight.
    Orchestrates Gemini Creative Intent, Deterministic Geometry & Solar Engines,
    and Parallel Location Constraints Research into a Spatial Production Brief.
    """

    def __init__(self):
        logger.info("Initializing StageSight workflow")

    async def execute(self, scene: SceneInput) -> SpatialProductionBrief:
        logger.info(f"Executing StageSight Agent for project: {scene.project_name} | {scene.scene_number}")

        # Step 1: Creative Intent Analysis (Gemini via the Google Gen AI SDK)
        creative_intent = await analyze_scene_intent(scene)

        # Step 2: Deterministic Optics & Camera Geometry Calculation
        # Determine average actor center
        if scene.floor_plan.actors:
            avg_x = sum(a.position.x for a in scene.floor_plan.actors) / len(scene.floor_plan.actors)
            avg_y = sum(a.position.y for a in scene.floor_plan.actors) / len(scene.floor_plan.actors)
            actor_center = Point2D(x=avg_x, y=avg_y)
        else:
            actor_center = Point2D(x=3.5, y=2.25)

        subject_dist = dist_point_to_point(scene.floor_plan.camera.position, actor_center)
        available_depth = scene.floor_plan.camera.position.x + subject_dist

        optics = calculate_optics(
            sensor_format=scene.floor_plan.camera.sensor_format,
            focal_length_mm=scene.floor_plan.camera.focal_length_mm,
            subject_distance_m=subject_dist,
            desired_framing=creative_intent.shot_framing or "Wide two-shot",
            available_room_depth_m=available_depth
        )

        # Step 3: Deterministic Solar Ephemeris & Window Direct-Sun Calculation
        solar = calculate_solar_position(
            lat=scene.latitude,
            lon=scene.longitude,
            target_date=scene.intended_date,
            target_time=scene.intended_time,
            windows=scene.floor_plan.windows
        )

        # Step 4: Parallel Search Location & Permit Research (Parallel Track API)
        location_report = await search_location_constraints_with_parallel(
            venue_name=scene.venue_name,
            address=scene.venue_address
        )

        # Step 5: Multi-Department Conflict Matrix Detection
        conflicts = detect_department_conflicts(
            scene=scene,
            optics=optics,
            solar=solar,
            location_report=location_report
        )

        # Step 6: Feasibility Score and Alternatives Generation
        score = calculate_feasibility_score(conflicts)
        alternatives = generate_production_alternatives(
            optics=optics,
            floor_plan=scene.floor_plan,
            conflicts=conflicts
        )

        # Step 7: Items Requiring Physical Confirmation
        physical_confirmations = [
            "Actual interior ambient illuminance (lux) through window with light meter",
            "Freight elevator door clearance and load bearing rating for heavy camera dolly",
            "Dedicated 32A 3-phase power drop availability in basement distribution board",
            "Physical verification of window frame latch integrity and exterior stand mounting"
        ]

        # Compile Verified Spatial Production Brief
        brief = SpatialProductionBrief(
            project_name=scene.project_name,
            scene_title=scene.scene_number,
            created_at=datetime.now().isoformat(),
            overall_feasibility_score=score,
            creative_intent=creative_intent,
            optics=optics,
            solar=solar,
            location_research=location_report,
            conflicts=conflicts,
            alternatives=alternatives,
            items_requiring_physical_confirmation=physical_confirmations
        )

        logger.info(f"Spatial Production Brief compiled successfully. Feasibility score: {score}%")
        return brief
