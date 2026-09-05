from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class Point2D(BaseModel):
    x: float
    y: float

class Wall(BaseModel):
    id: str
    start: Point2D
    end: Point2D
    thickness: float = 0.15

class Window(BaseModel):
    id: str
    wall_id: Optional[str] = None
    start: Point2D
    end: Point2D
    normal_angle_deg: float = 270.0  # 0=North, 90=East, 180=South, 270=West
    name: str = "West Bay Window"

class Door(BaseModel):
    id: str
    start: Point2D
    end: Point2D
    is_fire_exit: bool = False
    name: str = "Exit Door"
    clearance_radius: float = 1.0

class Furniture(BaseModel):
    id: str
    name: str
    x: float
    y: float
    width: float
    height: float
    rotation_deg: float = 0.0

class Actor(BaseModel):
    id: str
    name: str
    position: Point2D
    facing_direction_deg: float = 0.0
    clearance_radius: float = 0.6

class Camera(BaseModel):
    id: str = "cam1"
    position: Point2D
    target: Optional[Point2D] = None
    rotation_deg: float = 90.0  # Facing East
    sensor_format: str = "Full Frame 36x24"
    focal_length_mm: float = 35.0
    aspect_ratio: str = "16:9"
    height_m: float = 1.4

class DollyPath(BaseModel):
    id: str = "dolly1"
    waypoints: List[Point2D]
    width_m: float = 0.9

class LightingZone(BaseModel):
    id: str
    name: str
    position: Point2D
    width_m: float = 1.2
    height_m: float = 1.2
    fixture_type: str = "1.2x1.2m Diffusion Frame + C-Stand"

class CrewZone(BaseModel):
    id: str
    name: str
    position: Point2D
    radius_m: float = 1.0
    department: str = "Sound / Video Village"

class FloorPlan(BaseModel):
    width_m: float = 6.0
    depth_m: float = 4.5
    walls: List[Wall] = []
    windows: List[Window] = []
    doors: List[Door] = []
    furniture: List[Furniture] = []
    actors: List[Actor] = []
    camera: Camera = Camera(position=Point2D(x=1.2, y=2.25))
    dolly_path: Optional[DollyPath] = None
    lighting_zones: List[LightingZone] = []
    crew_zones: List[CrewZone] = []
    scale_calibration: str = "1m = 50px"

class SceneInput(BaseModel):
    project_name: str = "The Last Sunset"
    scene_number: str = "SCENE 14 - INT. DINING ROOM"
    script_text: str = (
        "INT. DINING ROOM - SUNSET\n"
        "Elena and Marcus sit across from each other at the heavy oak table. "
        "A warm, blinding amber sunset spills through the west window behind Elena, "
        "catching the steam rising from their untouched tea.\n"
        "The camera begins on a wide two-shot and slowly dollies closer, tightening "
        "on the mounting tension between them."
    )
    creative_intent: str = "A wide but intimate two-shot with a slow dolly movement and sunset backlight."
    intended_date: str = "2026-09-15"
    intended_time: str = "17:30"
    venue_name: str = "The Old Woolstore Dining Hall"
    venue_address: str = "14 Macquarie Street, Sydney NSW 2000, Australia"
    latitude: float = -33.8688
    longitude: float = 151.2093
    crew_size: int = 12
    floor_plan: FloorPlan = Field(default_factory=FloorPlan)

class CreativeIntentExtraction(BaseModel):
    scene_type: str
    mood: str
    shot_framing: str
    camera_movement: str
    lighting_requirement: str
    key_actors: List[str]
    raw_summary: str

class OpticsCalculation(BaseModel):
    sensor_format: str
    sensor_width_mm: float
    sensor_height_mm: float
    focal_length_mm: float
    horizontal_fov_deg: float
    vertical_fov_deg: float
    subject_distance_m: float
    required_frame_width_m: float
    minimum_camera_depth_m: float
    available_camera_depth_m: float
    depth_margin_m: float
    has_clearance_conflict: bool
    status: str = "CALCULATED"

class SolarCalculation(BaseModel):
    shoot_datetime: str
    sunrise_time: str
    sunset_time: str
    golden_hour_start: str
    golden_hour_end: str
    sun_azimuth_deg: float
    sun_elevation_deg: float
    direct_sun_opportunity: str
    expected_sun_window: str
    window_alignment_deg: float
    notes: str
    status: str = "CALCULATED"
    direct_sun_status: str = "ESTIMATED"

class ParallelCitation(BaseModel):
    title: str
    url: str
    excerpt: str
    source_type: str
    publication_date: Optional[str] = None
    retrieval_timestamp: str
    confidence_score: float
    verification_status: str = "VERIFIED"

class LocationConstraintsReport(BaseModel):
    venue_name: str
    council_area: str
    permit_requirements: str
    curfew_hours: str
    noise_limits: str
    parking_and_loading: str
    citations: List[ParallelCitation]
    # False when Parallel returned nothing, so the client can say "not
    # researched" instead of rendering empty fields as if they were findings.
    researched: bool = True
    note: str = ""

class DepartmentConflict(BaseModel):
    id: str
    department: str  # Director, DP, Gaffer, AD, Safety, Location
    severity: str    # HIGH, MEDIUM, LOW, RESOLVED
    title: str
    description: str
    physical_measurement: str
    suggested_fix: str

class ProductionAlternative(BaseModel):
    id: str
    category: str
    title: str
    impact_description: str
    tradeoffs: str
    recommended: bool = False

class SpatialProductionBrief(BaseModel):
    project_name: str
    scene_title: str
    created_at: str
    overall_feasibility_score: int  # 0 to 100
    creative_intent: CreativeIntentExtraction
    optics: OpticsCalculation
    solar: SolarCalculation
    location_research: LocationConstraintsReport
    conflicts: List[DepartmentConflict]
    alternatives: List[ProductionAlternative]
    items_requiring_physical_confirmation: List[str]
