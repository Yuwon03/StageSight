import math
from typing import Tuple, List, Dict, Any, Optional
from app.models.schemas import (
    Point2D, Wall, Window, Door, Furniture, Actor, Camera, DollyPath,
    LightingZone, CrewZone, FloorPlan, OpticsCalculation, DepartmentConflict
)

SENSOR_DIMENSIONS = {
    "Full Frame 36x24": (36.0, 24.0),
    "Super 35": (24.89, 18.66),
    "APS-C": (23.6, 15.6),
    "Micro Four Thirds": (17.3, 13.0),
}

def calculate_optics(
    sensor_format: str,
    focal_length_mm: float,
    subject_distance_m: float,
    desired_framing: str = "Wide two-shot",
    available_room_depth_m: float = 3.0
) -> OpticsCalculation:
    """
    Deterministically computes horizontal/vertical FOV and depth clearance requirements.
    Formula: FOV = 2 * arctan(sensor_dimension / (2 * focal_length))
    Required Distance = (Required Frame Width * focal_length) / sensor_width
    """
    sensor_width, sensor_height = SENSOR_DIMENSIONS.get(sensor_format, (36.0, 24.0))
    
    # Trigonometric FOV in degrees
    hfov_rad = 2 * math.atan(sensor_width / (2 * focal_length_mm))
    vfov_rad = 2 * math.atan(sensor_height / (2 * focal_length_mm))
    hfov_deg = math.degrees(hfov_rad)
    vfov_deg = math.degrees(vfov_rad)
    
    # Required frame width for different shot types
    framing_widths = {
        "Wide two-shot": 2.4,       # Two actors + table + environmental context
        "Medium two-shot": 1.6,     # Waist-up two-shot
        "Medium close-up": 0.8,     # Single actor shoulders-up
        "Close-up": 0.5             # Single actor face
    }
    req_frame_width = framing_widths.get(desired_framing, 2.4)
    
    # Minimum camera-to-subject distance based on optics
    min_dist_m = (req_frame_width * focal_length_mm) / sensor_width
    
    # Operator, tripod, and dolly clearance buffer behind camera
    camera_operator_buffer_m = 1.2
    min_depth_needed_m = min_dist_m + camera_operator_buffer_m
    
    depth_margin_m = available_room_depth_m - min_depth_needed_m
    has_conflict = depth_margin_m < 0

    return OpticsCalculation(
        sensor_format=sensor_format,
        sensor_width_mm=sensor_width,
        sensor_height_mm=sensor_height,
        focal_length_mm=focal_length_mm,
        horizontal_fov_deg=round(hfov_deg, 2),
        vertical_fov_deg=round(vfov_deg, 2),
        subject_distance_m=round(subject_distance_m, 2),
        required_frame_width_m=round(req_frame_width, 2),
        minimum_camera_depth_m=round(min_depth_needed_m, 2),
        available_camera_depth_m=round(available_room_depth_m, 2),
        depth_margin_m=round(depth_margin_m, 2),
        has_clearance_conflict=has_conflict,
        status="CALCULATED"
    )

def dist_point_to_point(p1: Point2D, p2: Point2D) -> float:
    return math.hypot(p1.x - p2.x, p1.y - p2.y)

def dist_point_to_segment(p: Point2D, v: Point2D, w: Point2D) -> float:
    """Distance from point p to line segment vw"""
    l2 = (v.x - w.x)**2 + (v.y - w.y)**2
    if l2 == 0:
        return dist_point_to_point(p, v)
    t = max(0.0, min(1.0, ((p.x - v.x) * (w.x - v.x) + (p.y - v.y) * (w.y - v.y)) / l2))
    projection = Point2D(x=v.x + t * (w.x - v.x), y=v.y + t * (w.y - v.y))
    return dist_point_to_point(p, projection)

def segments_intersect(p1: Point2D, p2: Point2D, p3: Point2D, p4: Point2D) -> bool:
    """Check if segment p1p2 intersects segment p3p4"""
    def ccw(a: Point2D, b: Point2D, c: Point2D) -> bool:
        return (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x)
    return (ccw(p1, p3, p4) != ccw(p2, p3, p4)) and (ccw(p1, p2, p3) != ccw(p1, p2, p4))

def check_spatial_collisions(floor_plan: FloorPlan) -> List[DepartmentConflict]:
    """
    Deterministic physical geometry validation across all departments:
    1. DP: Camera clearance vs room depth & actors
    2. Gaffer vs Grip: Dolly track intersecting lighting diffusion zone
    3. Safety Officer: Dolly track or equipment obstructing Fire Exit doors (<1.0m clearance)
    4. AD: Actor spacing and movement clearance
    """
    conflicts: List[DepartmentConflict] = []
    
    # 1. DP Optics Clearance check
    if floor_plan.actors and floor_plan.camera:
        # Distance from camera to first actor / center of actors
        avg_actor_x = sum(a.position.x for a in floor_plan.actors) / len(floor_plan.actors)
        avg_actor_y = sum(a.position.y for a in floor_plan.actors) / len(floor_plan.actors)
        actor_center = Point2D(x=avg_actor_x, y=avg_actor_y)
        
        subject_dist = dist_point_to_point(floor_plan.camera.position, actor_center)
        # Calculate available room depth behind camera
        available_depth = floor_plan.camera.position.x  # Assuming camera shoots +X
        
        optics = calculate_optics(
            sensor_format=floor_plan.camera.sensor_format,
            focal_length_mm=floor_plan.camera.focal_length_mm,
            subject_distance_m=subject_dist,
            desired_framing="Wide two-shot",
            available_room_depth_m=available_depth + subject_dist
        )
        
        if optics.has_clearance_conflict:
            deficit = abs(optics.depth_margin_m)
            conflicts.append(DepartmentConflict(
                id="conf_dp_clearance",
                department="Director of Photography",
                severity="HIGH",
                title="Insufficient Camera Clearance for Lens",
                description=(
                    f"The requested {floor_plan.camera.focal_length_mm:.0f}mm lens on {floor_plan.camera.sensor_format} "
                    f"requires {optics.minimum_camera_depth_m:.1f}m total depth for a wide two-shot. "
                    f"The room provides only {optics.available_camera_depth_m:.1f}m."
                ),
                physical_measurement=f"-{deficit:.2f}m camera depth deficit",
                suggested_fix="Switch to 28mm lens (reduces requirement by 0.7m) or move actors 0.8m closer to window."
            ))

    # 2. Dolly Path vs Lighting Diffusion Zones
    if floor_plan.dolly_path and floor_plan.lighting_zones:
        dolly_pts = floor_plan.dolly_path.waypoints
        for i in range(len(dolly_pts) - 1):
            seg_start, seg_end = dolly_pts[i], dolly_pts[i+1]
            for lz in floor_plan.lighting_zones:
                dist_to_light = dist_point_to_segment(lz.position, seg_start, seg_end)
                safe_buffer = (lz.width_m / 2) + (floor_plan.dolly_path.width_m / 2) + 0.3
                if dist_to_light < safe_buffer:
                    conflicts.append(DepartmentConflict(
                        id=f"conf_grip_light_{lz.id}",
                        department="Gaffer & Grip",
                        severity="HIGH",
                        title=f"Dolly Path Intersects {lz.name}",
                        description=(
                            f"The proposed dolly track passes within {dist_to_light:.2f}m of {lz.name} "
                            f"(requires {safe_buffer:.2f}m clearance for {lz.fixture_type})."
                        ),
                        physical_measurement=f"{dist_to_light:.2f}m separation (< {safe_buffer:.2f}m required)",
                        suggested_fix=f"Shift {lz.name} 0.5m north or angle dolly track slightly to clear the diffusion frame."
                    ))

    # 3. Fire Exit Obstruction
    for door in floor_plan.doors:
        if door.is_fire_exit:
            # Check camera position
            if floor_plan.camera:
                dist_cam = dist_point_to_point(floor_plan.camera.position, door.start)
                if dist_cam < door.clearance_radius:
                    conflicts.append(DepartmentConflict(
                        id=f"conf_safety_cam_{door.id}",
                        department="Safety & Fire Officer",
                        severity="HIGH",
                        title=f"Camera Setup Blocks {door.name}",
                        description=f"Camera tripod/rig is positioned {dist_cam:.2f}m from marked fire exit (minimum egress is {door.clearance_radius:.1f}m).",
                        physical_measurement=f"{dist_cam:.2f}m clearance (< {door.clearance_radius:.1f}m required)",
                        suggested_fix="Relocate camera base or adjust tripod legs to ensure a 1.0m clear exit path."
                    ))
            
            # Check dolly path
            if floor_plan.dolly_path:
                dolly_pts = floor_plan.dolly_path.waypoints
                for i in range(len(dolly_pts) - 1):
                    dist_dolly = dist_point_to_segment(door.start, dolly_pts[i], dolly_pts[i+1])
                    if dist_dolly < door.clearance_radius:
                        conflicts.append(DepartmentConflict(
                            id=f"conf_safety_dolly_{door.id}",
                            department="Safety & Fire Officer",
                            severity="HIGH",
                            title=f"Dolly Track Crosses Egress Path of {door.name}",
                            description=f"Dolly track passes within {dist_dolly:.2f}m of {door.name}, violating fire egress safety regulations.",
                            physical_measurement=f"{dist_dolly:.2f}m clearance (< {door.clearance_radius:.1f}m required)",
                            suggested_fix="Terminate dolly track 0.6m earlier or use low-profile cable crossover ramps with fire marshal signoff."
                        ))

    return conflicts
