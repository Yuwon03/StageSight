import {
  KoreanLocation,
  ScriptAnalysisResponse,
  ChatResponse,
  SceneInputData,
  SpatialProductionBrief,
 ParallelCitation } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

/**
 * There are deliberately NO offline fallbacks in this module. Every listing the
 * app shows is a real hourplace.co.kr listing served by the backend. When the
 * backend is unreachable these functions throw and the UI says so, because an
 * empty catalog is honest and an invented one is not.
 */
export class BackendUnreachableError extends Error {
  constructor(cause?: unknown) {
    super("백엔드 서버에 연결할 수 없습니다");
    this.name = "BackendUnreachableError";
    this.cause = cause;
  }
}

async function getJSON<T>(path: string, init?: RequestInit): Promise<{ data: T; res: Response }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (err) {
    throw new BackendUnreachableError(err);
  }
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return { data: (await res.json()) as T, res };
}

// External listing photos may block hotlinking — route them through the backend
// image proxy. Data URLs (AI renders) are used directly.
export function resolveImageUrl(url: string): string {
  if (!url) return url;
  if (url.startsWith("data:")) return url;
  return `${API_BASE}/api/image-proxy?url=${encodeURIComponent(url)}`;
}

// ── Catalog ─────────────────────────────────────────────────────────────────
export interface LocationQuery {
  category?: string;
  region?: string;
  max_price?: number;
  window_dir?: string;
  min_parking?: number;
  skip?: number;
  limit?: number;
  provider?: string;
  /** Defaults to "bookable" server-side; pass "전체" to include reference records. */
  listing_kind?: string;
}

function toParams(params?: LocationQuery): string {
  const sp = new URLSearchParams();
  if (params?.category && params.category !== "전체") sp.set("category", params.category);
  if (params?.region && params.region !== "전체") sp.set("region", params.region);
  if (params?.max_price) sp.set("max_price", String(params.max_price));
  if (params?.window_dir && params.window_dir !== "전체") sp.set("window_dir", params.window_dir);
  if (params?.min_parking) sp.set("min_parking", String(params.min_parking));
  if (params?.skip !== undefined) sp.set("skip", String(params.skip));
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  return sp.toString();
}

export async function fetchLocationsPage(
  params?: LocationQuery
): Promise<{ items: KoreanLocation[]; total: number; version: number }> {
  const { data, res } = await getJSON<KoreanLocation[]>(`/api/locations?${toParams(params)}`);
  const total = parseInt(res.headers.get("X-Total-Count") || `${data.length}`, 10);
  const version = parseInt(res.headers.get("X-Catalog-Version") || "0", 10);
  return { items: data, total, version };
}

export interface CatalogDelta {
  version: number;
  truncated: boolean;
  upserted: KoreanLocation[];
  removed: string[];
  catalog_size: number;
  new_count: number;
}

/** Asks the server only for what changed above `since` — no full refetch. */
export async function syncCatalog(since: number): Promise<CatalogDelta> {
  const { data } = await getJSON<CatalogDelta>(`/api/locations/sync?since=${since}`);
  return data;
}

export interface CatalogStats {
  live: number;
  delisted: number;
  new_within_72h: number;
  version: number;
  last_crawl: string;
  crawl_status: string;
  /** True when the server's catalogue is a read-only snapshot baked into its
   *  image (Cloud Run has no persistent disk), so ingestion cannot be run
   *  from the browser and would silently achieve nothing. */
  snapshot?: boolean;
  snapshot_taken_at?: string | null;
}

/** Catalogue size and whether it is live or a baked snapshot. */
export async function fetchCatalogStats(): Promise<CatalogStats> {
  const { data } = await getJSON<CatalogStats>("/api/locations/stats");
  return data;
}


export interface UploadedScript {
  filename: string;
  kind: "pdf" | "docx";
  pages: number;
  chars: number;
  truncated: boolean;
  warnings: string[];
  script_text: string;
}

/** Uploads a PDF/Word screenplay. The server validates type and content. */
export async function uploadScriptFile(file: File): Promise<UploadedScript> {
  const body = new FormData();
  body.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/script/upload`, { method: "POST", body });
  } catch (err) {
    throw new BackendUnreachableError(err);
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `업로드 실패 (HTTP ${res.status})`);
  }
  return res.json();
}


export async function fetchLocationById(id: string): Promise<KoreanLocation> {
  const { data } = await getJSON<KoreanLocation>(`/api/locations/${id}`);
  return data;
}

// ── Ingestion of real listings ──────────────────────────────────────────────
export interface IngestStatus {
  running: boolean;
  phase: string;
  ingested: number;
  target: number;
  total_known: number;
  catalog_size: number;
  error: string | null;
}

export async function startIngest(target = 1200, concurrency = 8): Promise<{ started: boolean; target: number }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/locations/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, concurrency }),
    });
  } catch (err) {
    throw new BackendUnreachableError(err);
  }
  if (res.status === 409) throw new Error("INGEST_ALREADY_RUNNING");
  if (!res.ok) throw new Error(`Ingest failed: HTTP ${res.status}`);
  return res.json();
}

export async function getIngestStatus(): Promise<IngestStatus> {
  const { data } = await getJSON<IngestStatus>("/api/locations/ingest/status");
  return data;
}

// ── Script matching & scouting chat ─────────────────────────────────────────
export async function matchScript(
  scriptText: string,
  projectTitle = "마지막 일몰",
  language: "ko" | "en" = "ko"
): Promise<ScriptAnalysisResponse> {
  const { data } = await getJSON<ScriptAnalysisResponse>("/api/script/match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script_text: scriptText, project_title: projectTitle, language }),
  });
  return data;
}

export async function sendScoutingChatMessage(
  messages: { role: string; content: string }[],
  currentSceneContext?: string,
  selectedLocId?: string,
  /** A passage the user highlighted in their own screenplay. */
  scriptExcerpt?: string,
  language: "ko" | "en" = "ko"
): Promise<ChatResponse> {
  const { data } = await getJSON<ChatResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      current_scene_context: currentSceneContext,
      selected_location_id: selectedLocId,
      script_excerpt: scriptExcerpt,
      language,
    }),
  });
  return data;
}

// ── AI frame simulation (orbit rig + relight) ───────────────────────────────
export interface AIFrameResult {
  /** Which tier produced this frame. */
  image_tier?: string;
  image_data_url: string;
  cached: boolean;
  model: string;
  focal_length_mm: number;
  /** Measured server-side, not guessed: false means the render is a crop or pan
   *  of the source rather than a camera that travelled. null when not checked. */
  camera_moved: boolean | null;
}

export class GeminiKeyMissingError extends Error {
  constructor() {
    super("GEMINI_API_KEY_NOT_CONFIGURED");
    this.name = "GeminiKeyMissingError";
  }
}

/** The source allows displaying this photograph but not altering it, so the
 *  frame simulator cannot run on it. Raised from a 451. */
export class LicenseNoDerivativesError extends Error {
  constructor() {
    super("LICENSE_NO_DERIVATIVES");
    this.name = "LicenseNoDerivativesError";
  }
}

export async function simulateAIFrame(
  params: {
    image_url: string;
    rotation: number;
    tilt: number;
    zoom: number;
    time_label: string;
    light_phase: string;
    phase_description?: string;
    window_direction?: string;
    date_label?: string;
    sun_altitude_deg?: number;
    space_category?: string;
    /** Lets the server check the photo's licence before spending a Gemini call. */
    location_id?: string;
    /** "fast" (default) or "detail". Latency and output resolution differ;
     *  measured render quality does not — see app/gemini_models.py. */
    image_tier?: "fast" | "detail";
  },
  signal?: AbortSignal
): Promise<AIFrameResult> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/simulate/frame`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
      signal,
    });
  } catch (err) {
    throw new BackendUnreachableError(err);
  }
  if (res.status === 503) throw new GeminiKeyMissingError();
  // 451: the source licenses this photo for display but forbids altering it.
  if (res.status === 451) throw new LicenseNoDerivativesError();
  if (!res.ok) throw new Error(`AI frame simulation failed: HTTP ${res.status}`);
  return res.json();
}

// ── Spatial production brief (deterministic geometry + solar + Parallel) ────

// ── Sources ─────────────────────────────────────────────────────────────────
export interface ProviderInfo {
  provider: string;
  label: string;
  site_url: string;
  /** public_open_data | partner_approved | robots_allowed | pending_permission */
  rights_status: string;
  listing_kind: string;
  enabled: boolean;
  /** Who has to grant permission before this source can run. */
  blocked_on?: string | null;
  note?: string;
  counts: Record<string, number>;
}

/** Live sources with their counts, and the ones still awaiting permission. */
export async function fetchProviders(): Promise<ProviderInfo[]> {
  const { data } = await getJSON<{ providers: ProviderInfo[] }>("/api/providers");
  return data.providers;
}

// ── Filming-permit research (Parallel Search API) ───────────────────────────
export interface PermitReport {
  venue_name: string;
  council_area: string;
  permit_requirements: string;
  curfew_hours: string;
  noise_limits: string;
  parking_and_loading: string;
  citations: ParallelCitation[];
  /** False when Parallel returned nothing at all. */
  researched: boolean;
  /** Why a field is blank, when it is. */
  note?: string;
}

/**
 * Researches what it actually takes to film at this address — permits, curfew,
 * noise limits, loading — via the Parallel Search API, and returns the sources
 * it read. Run on demand rather than on page load: it is a live web research
 * call taking tens of seconds, and most visitors are browsing, not booking.
 */
export async function researchFilmingPermits(
  venueName: string,
  address: string,
  councilArea: string,
  signal?: AbortSignal,
  language: "en" | "ko" = "ko"
): Promise<PermitReport> {
  const q = new URLSearchParams({
    venue_name: venueName,
    address,
    council_area: councilArea,
    language,
  });
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/parallel/search?${q}`, { method: "POST", signal });
  } catch (err) {
    throw new BackendUnreachableError(err);
  }
  if (!res.ok) throw new Error(`Permit research failed: HTTP ${res.status}`);
  return res.json();
}
