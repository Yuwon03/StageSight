export interface Point2D {
  x: number;
  y: number;
}

export interface ParallelCitation {
  title: string;
  url: string;
  excerpt: string;
  source_type: string;
  publication_date?: string;
  retrieval_timestamp: string;
  confidence_score: number;
  verification_status: string;
}

export interface LocationSpec {
  area_sqm: number;
  area_pyeong: number;
  ceiling_height_m: number;
  window_direction: string;
  natural_light_type: string;
  golden_hour_window: string;
  power_capacity: string;
  parking_spots: number;
  has_freight_elevator: boolean;
  sound_recording_quality: string;
}

export interface KoreanLocation {
  id: string;
  name: string;
  tagline: string;
  region: string;
  region_category: string;
  category: string;
  price_per_hour: number;
  price_per_day: number;
  min_hours: number;
  rating: number;
  review_count: number;
  images: string[];
  specs: LocationSpec;
  tags: string[];
  permit_summary: string;
  citations: ParallelCitation[];
  /** Server-derived: first seen within the last 72 hours. Drives the NEW badge. */
  is_new?: boolean;
  first_seen?: string;

  // ── Provenance (multi-source catalogue) ──────────────────────────────────
  /** Which source supplied this row: "hourplace", "public_data", … */
  provider?: string;
  provider_listing_id?: string;
  source_url?: string;
  /** bookable | inquiry_only | reference. A `reference` row is a real place
   *  films have used, not something anyone can book — the UI must not offer it
   *  as a listing. */
  listing_kind?: string;
  /** Shared by every listing of the same physical venue across platforms. */
  canonical_id?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  /** public_open_data | partner_approved | robots_allowed */
  rights_status?: string;
  /** The photo may be displayed with attribution but not altered, so the AI
   *  frame simulator is unavailable for this listing. */
  no_derivatives?: boolean;
}

export interface SceneMatchResult {
  scene_number: string;
  scene_title: string;
  scene_summary: string;
  mood: string;
  time_of_day: string;
  required_space_type: string;
  recommended_location_ids: string[];
  ai_recommendation_reason: string;
  primary_location: KoreanLocation;
  alternative_location?: KoreanLocation;
}

export interface ScriptAnalysisResponse {
  project_title: string;
  total_scenes_detected: number;
  scenes: SceneMatchResult[];
  overall_production_advice: string;
  /** Short name for the conversation, produced by the same Gemini call
   *  that matched the scenes — naming a thread is not worth a second call. */
  thread_title?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  suggested_locations?: KoreanLocation[];
  applied_filter_summary?: string;
  /** Model that produced an assistant turn, rendered as a small byline. */
  model?: string;
  /** A passage the user attached from their screenplay with this turn. */
  script_excerpt?: string;
  /** Set when the turn failed, so the transcript shows the failure honestly
   *  instead of an assistant-looking apology. */
  error?: boolean;
}

export interface ChatResponse {
  reply: string;
  suggested_locations: KoreanLocation[];
  applied_filter_summary?: string;
  /** Which Gemini model answered. Shown in the UI so a real answer is
   *  distinguishable from a canned one — this endpoint used to be canned. */
  model?: string;
}

export interface Wall {
  id: string;
  start: Point2D;
  end: Point2D;
  thickness: number;
}

export interface WindowItem {
  id: string;
  wall_id?: string;
  start: Point2D;
  end: Point2D;
  normal_angle_deg: number;
  name: string;
}

export interface DoorItem {
  id: string;
  start: Point2D;
  end: Point2D;
  is_fire_exit: boolean;
  name: string;
  clearance_radius: number;
}

export interface FurnitureItem {
  id: string;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation_deg: number;
}

export interface ActorItem {
  id: string;
  name: string;
  position: Point2D;
  facing_direction_deg: number;
  clearance_radius: number;
}

export interface CameraItem {
  id: string;
  position: Point2D;
  target?: Point2D;
  rotation_deg: number;
  sensor_format: string;
  focal_length_mm: number;
  aspect_ratio: string;
  height_m: number;
}

export interface DollyPathItem {
  id: string;
  waypoints: Point2D[];
  width_m: number;
}

export interface LightingZoneItem {
  id: string;
  name: string;
  position: Point2D;
  width_m: number;
  height_m: number;
  fixture_type: string;
}

export interface CrewZoneItem {
  id: string;
  name: string;
  position: Point2D;
  radius_m: number;
  department: string;
}

export interface FloorPlanData {
  width_m: number;
  depth_m: number;
  walls: Wall[];
  windows: WindowItem[];
  doors: DoorItem[];
  furniture: FurnitureItem[];
  actors: ActorItem[];
  camera: CameraItem;
  dolly_path?: DollyPathItem;
  lighting_zones: LightingZoneItem[];
  crew_zones: CrewZoneItem[];
  scale_calibration: string;
}

export interface SceneInputData {
  project_name: string;
  scene_number: string;
  script_text: string;
  creative_intent: string;
  intended_date: string;
  intended_time: string;
  venue_name: string;
  venue_address: string;
  latitude: number;
  longitude: number;
  crew_size: number;
  floor_plan: FloorPlanData;
}

export interface CreativeIntentExtraction {
  scene_type: string;
  mood: string;
  shot_framing: string;
  camera_movement: string;
  lighting_requirement: string;
  key_actors: string[];
  raw_summary: string;
}

export interface OpticsCalculation {
  sensor_format: string;
  sensor_width_mm: number;
  sensor_height_mm: number;
  focal_length_mm: number;
  horizontal_fov_deg: number;
  vertical_fov_deg: number;
  subject_distance_m: number;
  required_frame_width_m: number;
  minimum_camera_depth_m: number;
  available_camera_depth_m: number;
  depth_margin_m: number;
  has_clearance_conflict: boolean;
  status: string;
}

export interface SolarCalculation {
  shoot_datetime: string;
  sunrise_time: string;
  sunset_time: string;
  golden_hour_start: string;
  golden_hour_end: string;
  sun_azimuth_deg: number;
  sun_elevation_deg: number;
  direct_sun_opportunity: string;
  expected_sun_window: string;
  window_alignment_deg: number;
  notes: string;
  status: string;
  direct_sun_status: string;
}

export interface LocationConstraintsReport {
  venue_name: string;
  council_area: string;
  permit_requirements: string;
  curfew_hours: string;
  noise_limits: string;
  parking_and_loading: string;
  citations: ParallelCitation[];
}

export interface DepartmentConflict {
  id: string;
  department: string;
  severity: "HIGH" | "MEDIUM" | "LOW" | "RESOLVED";
  title: string;
  description: string;
  physical_measurement: string;
  suggested_fix: string;
}

export interface ProductionAlternative {
  id: string;
  category: string;
  title: string;
  impact_description: string;
  tradeoffs: string;
  recommended: boolean;
}

export interface SpatialProductionBrief {
  project_name: string;
  scene_title: string;
  created_at: string;
  overall_feasibility_score: number;
  creative_intent: CreativeIntentExtraction;
  optics: OpticsCalculation;
  solar: SolarCalculation;
  location_research: LocationConstraintsReport;
  conflicts: DepartmentConflict[];
  alternatives: ProductionAlternative[];
  items_requiring_physical_confirmation: string[];
}
