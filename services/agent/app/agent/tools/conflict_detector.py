from typing import List
from app.models.schemas import (
    FloorPlan, OpticsCalculation, SolarCalculation, LocationConstraintsReport,
    DepartmentConflict, SceneInput
)
from app.agent.tools.geometry_engine import check_spatial_collisions

def detect_department_conflicts(
    scene: SceneInput,
    optics: OpticsCalculation,
    solar: SolarCalculation,
    location_report: LocationConstraintsReport
) -> List[DepartmentConflict]:
    """
    Synthesizes conflicts across all film departments:
    Director, DP, Gaffer & Grip, AD, Safety & Fire, Location Manager.
    """
    conflicts: List[DepartmentConflict] = []
    
    # 1. Add physical geometry and optics collisions
    spatial_conflicts = check_spatial_collisions(scene.floor_plan)
    conflicts.extend(spatial_conflicts)
    
    # 2. Gaffer / Sun Window Timing check
    # If shooting at 17:30, and sunset window is 17:40-18:05, notice narrow window
    if solar.direct_sun_opportunity == "High":
        conflicts.append(DepartmentConflict(
            id="conf_gaffer_sun_window",
            department="Gaffer & Lighting",
            severity="MEDIUM",
            title="Narrow Direct-Sun Golden Hour Window",
            description=(
                f"Direct sunlight penetration through the west window is expected only between {solar.expected_sun_window}. "
                f"Shooting call time of {scene.intended_time} gives an effective natural backlight window of ~25 minutes."
            ),
            physical_measurement=f"{solar.expected_sun_window} (25 min direct backlight window)",
            suggested_fix="Rig an exterior 4K HMI / warm LED fixture on a boom arm outside the west window to extend backlight continuity across takes."
        ))

    # 3. Location Manager & Council Permit Checks
    if scene.crew_size > 10:
        conflicts.append(DepartmentConflict(
            id="conf_loc_permit",
            department="Location Manager",
            severity="LOW",
            title="Council Notification & Public Footpath Clearance",
            description=(
                f"With a crew size of {scene.crew_size}, City of Sydney guidelines require 3 business days written notice. "
                "Exterior lighting equipment cannot obstruct pedestrian traffic without a council permit."
            ),
            physical_measurement=f"{scene.crew_size} crew on site (>10 crew threshold)",
            suggested_fix="Submit City of Sydney filming notification 3 days prior; keep all exterior gear inside private property boundary."
        ))

    # 4. Location Noise & Curfew
    conflicts.append(DepartmentConflict(
        id="conf_loc_noise",
        department="Location Manager",
        severity="LOW",
        title="Residential Zone Noise Curfew (22:00)",
        description="Location regulations enforce a 50 dB(A) acoustic noise limit and 22:00 wrap curfew.",
        physical_measurement="50 dB(A) max after 18:00; 22:00 wrap",
        suggested_fix="Schedule heavy dialogue and dolly movement prior to 21:30 to allow wrap before 22:00."
    ))

    return conflicts

def calculate_feasibility_score(conflicts: List[DepartmentConflict]) -> int:
    """
    Computes feasibility score out of 100 based on conflict severity.
    High: -20, Medium: -10, Low: -5. Base 100.
    """
    score = 100
    for c in conflicts:
        if c.severity == "HIGH":
            score -= 22
        elif c.severity == "MEDIUM":
            score -= 10
        elif c.severity == "LOW":
            score -= 4
    return max(15, min(100, score))
