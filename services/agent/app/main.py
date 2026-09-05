import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional

logger = logging.getLogger(__name__)
from app.config import settings
from app.models.schemas import (
    SceneInput, SpatialProductionBrief, OpticsCalculation, SolarCalculation,
    LocationConstraintsReport
)
from app.models.korean_locations import KoreanLocation
from app.agent.workflow import StageSightAgentWorkflow
from app.agent.tools.geometry_engine import calculate_optics
from app.agent.tools.solar_engine import calculate_solar_position
from app.agent.tools.parallel_search import search_location_constraints_with_parallel
from app.agent.tools.script_matcher import (
    analyze_script_and_match_locations,
    chat_with_script_ai,
    ScriptAnalysisResponse,
    ChatRequest,
    ChatResponse,
    DEFAULT_KOREAN_SCRIPT
)

app = FastAPI(
    title="StageSight - Korean Filming Locations & Spatial Production Intelligence",
    description="Korean filming-location scouting with Gemini, deterministic production tools, and Parallel Search",
    version="2.0.0"
)

# CORS middleware for local and Cloud Run frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

workflow = StageSightAgentWorkflow()

@app.get("/")
def read_root():
    return {
        "service": "StageSight Spatial Production Planning & Korean Location Platform",
        "status": "operational",
        "runtime_ai": "Gemini via the Google Gen AI SDK",
        "agent_workflow": "Custom Python multi-step workflow",
        "track_integration": "Parallel Search API (parallel-web SDK)",
        "locations_available": store.count(),
        "location_source": "Multiple Korean listing and public-data providers; no synthetic listings",
        "version": "3.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# ==============================================================================
# Korean Filming Locations Catalog Endpoints
# ==============================================================================

from fastapi.responses import Response
from fastapi import BackgroundTasks
from pydantic import BaseModel

from app import store
from app.agent.tools import hourplace_ingest

# Listings live in SQLite, shared with the crawler process. Nothing is loaded
# into memory here: the API answers from the DB so a running crawl never blocks
# a request, and a restart never loses the catalog.
store.init_db()


@app.get("/api/locations")
def get_locations(
    response: Response,
    category: Optional[str] = Query(None, description="모던 스튜디오 / 전통 한옥 / 자연·야외 / 빈티지·창고 / 럭셔리 하우스 / 카페·갤러리"),
    region: Optional[str] = Query(None, description="서울 / 경기 / 인천 / 부산 / 제주 …"),
    max_price: Optional[int] = Query(None, description="Max price per hour in KRW"),
    window_dir: Optional[str] = Query(None, description="서향 / 남향 / 북향 / 동향"),
    min_parking: Optional[int] = Query(None, description="Minimum parking spots"),
    provider: Optional[str] = Query(None, description="hourplace / public_data / …"),
    listing_kind: Optional[str] = Query(
        "bookable",
        description="bookable (기본) / inquiry_only / reference / 전체. "
                    "reference 는 과거 촬영 기록이며 대관 가능 매물이 아닙니다.",
    ),
    skip: int = Query(0),
    limit: int = Query(60),
):
    """
    Real listings, newest first. Each carries `is_new` (first seen within 72h) so
    the client can badge fresh finds without doing date maths of its own.
    X-Total-Count is the total match count; X-Catalog-Version is the store
    revision the client should pass to /api/locations/sync next time.
    """
    items, total = store.search(
        category=category, region=region, max_price=max_price,
        window_dir=window_dir, min_parking=min_parking,
        provider=provider, listing_kind=listing_kind,
        skip=skip, limit=min(limit, 200),
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Catalog-Version"] = str(store.current_rev())
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Catalog-Version"
    return items


@app.get("/api/locations/sync")
def sync_locations(since: int = Query(0, description="Last catalog version the client holds")):
    """
    Delta sync. Returns only what changed above `since`, so a client that already
    has the catalog refreshes in one small response instead of refetching it all.
    `truncated` means there is more — call again with the returned version.
    """
    return store.changes_since(max(0, since))


@app.get("/api/locations/stats")
def catalog_stats():
    return {
        **store.stats(),
        "providers": store.provider_breakdown(),
        # The deployed catalogue is a snapshot baked into the image; the UI has
        # to be able to say so rather than implying it is live.
        "snapshot": store.is_ephemeral(),
        "snapshot_taken_at": store.snapshot_taken_at(),
    }


@app.get("/api/providers")
def list_providers():
    """Every source: live ones with their counts, and the ones still blocked.

    The roadmap is served rather than kept in a comment so the UI can say where
    a listing came from and, just as importantly, which platforms are not being
    crawled and what is blocking them — permission, not capability.
    """
    import sys
    from pathlib import Path

    # app/main.py → app → agent → services, so the sibling crawler package.
    crawler = str(Path(__file__).resolve().parents[2] / "crawler")
    if crawler not in sys.path:
        sys.path.insert(0, crawler)
    try:
        from providers.registry import roadmap  # noqa: E402
    except ImportError:
        # The API can serve listings without the crawler package present (they
        # deploy as separate containers); it just cannot describe the roadmap.
        return {"providers": [
            {"provider": r["provider"], "label": r["provider"], "site_url": "",
             "rights_status": r["rights_status"], "listing_kind": r["listing_kind"],
             "enabled": True, "blocked_on": None, "counts": {r["listing_kind"]: r["count"]}}
            for r in store.provider_breakdown()
        ]}

    counts = {}
    for row in store.provider_breakdown():
        counts.setdefault(row["provider"], {})[row["listing_kind"]] = row["count"]

    out = []
    for r in roadmap():
        out.append({**r, "counts": counts.get(r["provider"], {})})
    return {"providers": out}


@app.get("/api/locations/{location_id}")
def get_location_by_id(location_id: str):
    loc = store.by_id(location_id)
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return loc


class IngestPayload(BaseModel):
    target: int = 800          # how many real listings to pull in this run
    concurrency: int = 6       # parallel fetches against hourplace.co.kr


@app.post("/api/locations/ingest")
async def ingest_real_locations(payload: IngestPayload, background: BackgroundTasks):
    """
    On-demand ingest for a cold start. Steady-state updates are the crawler
    process's job (services/crawler/worker.py); this exists so a fresh install
    has listings without waiting for the first scheduled pass.
    """
    # On Cloud Run the write would go to an in-memory filesystem that dies with
    # the instance, so this would crawl a third-party site for nothing and show
    # the user a progress bar that achieves nothing. Refuse instead of pretending.
    if store.is_ephemeral():
        raise HTTPException(
            status_code=409,
            detail="INGEST_UNAVAILABLE_ON_SNAPSHOT: 배포본의 카탈로그는 이미지에 포함된 "
                   "스냅샷이라 수집 결과를 보관할 수 없습니다. 수집은 로컬에서 실행한 뒤 "
                   "재배포해야 반영됩니다.",
        )
    if hourplace_ingest.STATE.get("running"):
        raise HTTPException(status_code=409, detail="INGEST_ALREADY_RUNNING")

    target = max(1, min(13000, payload.target))

    async def run():
        await hourplace_ingest.ingest(
            target=target,
            concurrency=max(1, min(12, payload.concurrency)),
            on_batch=lambda batch: store.upsert_many(batch),
        )

    background.add_task(run)
    return {"started": True, "target": target, "already_have": store.count()}


@app.get("/api/locations/ingest/status")
def ingest_status():
    return {**hourplace_ingest.STATE, "catalog_size": store.count()}

from app.image_cache import fetch_cached, cache_stats


@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(..., description="Target image URL to proxy")):
    """
    Proxies listing photos (the CDNs block hotlinking) through a disk cache, so a
    photo is pulled from the origin once and served locally from then on.
    """
    hit = await fetch_cached(url)
    if hit is None:
        raise HTTPException(status_code=404, detail="Image could not be retrieved")
    body, content_type, from_cache = hit
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Cache": "HIT" if from_cache else "MISS",
        },
    )


@app.get("/api/image-cache/stats")
def image_cache_stats():
    return cache_stats()

# ==============================================================================
# AI Frame Simulator (Gemini relight + lens re-framing)
# ==============================================================================

from app.agent.tools.frame_simulator import (
    FrameSimRequest, FrameSimResponse, simulate_frame_with_gemini
)
from app.agent.tools.frame_prompt import PROMPT_VERSION


@app.get("/api/simulate/prompt-version")
def simulate_prompt_version():
    """Which prompt revision this process has loaded.

    uvicorn runs without --reload, so editing frame_prompt.py has no effect
    until the server restarts. An eval round pointed at a stale process measures
    the previous prompt and gets written up as the new one; evaluation/run_eval
    checks this before it starts and refuses to run on a mismatch.
    """
    return {"prompt_version": PROMPT_VERSION}

@app.post("/api/simulate/frame", response_model=FrameSimResponse)
async def simulate_frame(req: FrameSimRequest):
    """
    Re-renders a location photo at a different time-of-day / lens using Gemini
    image generation. Returns 503 with a machine-readable detail when the
    GEMINI_API_KEY is not configured so the frontend can fall back to its
    physics-based approximation.
    """
    # Licence check before any model call. 한국관광공사 marks 55% of its
    # photographs cpyrhtDivCd=Type3 (제1유형 + 변경금지) — free to display,
    # forbidden to alter. This endpoint's entire job is altering them, so it
    # refuses rather than trusting the UI to hide the button.
    if req.location_id:
        row = store.by_id(req.location_id)
        if row and row.get("no_derivatives"):
            raise HTTPException(
                status_code=451,
                detail="LICENSE_NO_DERIVATIVES: 이 사진은 출처 표시 후 게시만 허용되고 "
                       "변경이 금지되어 있어 AI 재생성을 할 수 없습니다.",
            )

    try:
        return await simulate_frame_with_gemini(req)
    except RuntimeError as e:
        if "GEMINI_API_KEY_NOT_CONFIGURED" in str(e):
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY_NOT_CONFIGURED")
        logger.warning(f"Frame simulation failed: {e}")
        raise HTTPException(status_code=502, detail=f"FRAME_SIMULATION_FAILED: {e}")
    except Exception as e:
        logger.warning(f"Frame simulation error: {type(e).__name__}: {e!r}")
        raise HTTPException(status_code=502, detail=f"FRAME_SIMULATION_FAILED: {e}")

# ==============================================================================
# Script AI Scene Matching & Multi-Turn Chat Endpoints
# ==============================================================================

class ScriptMatchRequest(BaseModel if "BaseModel" in globals() else object):
    pass

from pydantic import BaseModel
class ScriptRequestPayload(BaseModel):
    script_text: str = DEFAULT_KOREAN_SCRIPT
    project_title: str = "마지막 일몰 (The Last Sunset)"

from fastapi import File, UploadFile
from app.script_upload import extract_script, UploadRejected, MAX_UPLOAD_BYTES


@app.post("/api/script/upload")
async def upload_script_file(file: UploadFile = File(...)):
    """
    Extracts screenplay text from a PDF or Word upload.

    The real file type is decided by magic bytes, not the extension or the
    client-supplied content-type. Macro-bearing Word files, PDFs with active
    content, encrypted files and zip bombs are refused — see app/script_upload.py.
    """
    # Read with a hard ceiling so an oversized upload can't exhaust memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        result = extract_script(data, file.filename or "")
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning(f"script upload failed: {e!r}")
        raise HTTPException(status_code=400, detail="파일을 처리할 수 없습니다.")

    return {
        "filename": file.filename,
        "kind": result.kind,
        "pages": result.pages,
        "chars": len(result.text),
        "truncated": result.truncated,
        "warnings": result.warnings,
        "script_text": result.text,
    }


@app.post("/api/script/match", response_model=ScriptAnalysisResponse)
async def match_script_scenes(payload: ScriptRequestPayload):
    """
    Uses Gemini through the Google Gen AI SDK to extract scene requirements and
    match them against real catalogue records.
    """
    return await analyze_script_and_match_locations(payload.script_text, payload.project_title)

@app.post("/api/chat", response_model=ChatResponse)
async def chat_scouting_assistant(req: ChatRequest):
    """
    Multi-turn conversational AI scouting assistant.

    This is a real Gemini call over the real catalog. It used to be a keyword
    router returning fixed sentences, which was indistinguishable from an AI in
    the UI — so when the model is unreachable this now fails loudly instead of
    degrading into something that only looks like an answer.
    """
    try:
        return await chat_with_script_ai(req)
    except RuntimeError as e:
        if "GEMINI_API_KEY_NOT_CONFIGURED" in str(e):
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY_NOT_CONFIGURED")
        logger.warning(f"chat failed: {e}")
        raise HTTPException(status_code=502, detail=f"CHAT_FAILED: {e}")
    except Exception as e:
        logger.warning(f"chat error: {type(e).__name__}: {e!r}")
        raise HTTPException(status_code=502, detail=f"CHAT_FAILED: {e}")

# ==============================================================================
# Deterministic Spatial & Parallel Endpoints
# ==============================================================================

@app.post("/api/analyze", response_model=SpatialProductionBrief)
async def analyze_scene(scene: SceneInput):
    try:
        brief = await workflow.execute(scene)
        return brief
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent workflow error: {str(e)}")

@app.post("/api/optics", response_model=OpticsCalculation)
def get_optics_calculation(
    sensor_format: str = "Full Frame 36x24",
    focal_length_mm: float = 35.0,
    subject_distance_m: float = 2.4,
    desired_framing: str = "Wide two-shot",
    available_room_depth_m: float = 3.2
):
    return calculate_optics(
        sensor_format=sensor_format,
        focal_length_mm=focal_length_mm,
        subject_distance_m=subject_distance_m,
        desired_framing=desired_framing,
        available_room_depth_m=available_room_depth_m
    )

@app.post("/api/solar", response_model=SolarCalculation)
def get_solar_calculation(
    lat: float = 37.5665,
    lon: float = 126.9780,
    target_date: str = "2026-09-15",
    target_time: str = "17:30"
):
    return calculate_solar_position(
        lat=lat,
        lon=lon,
        target_date=target_date,
        target_time=target_time
    )

@app.post("/api/parallel/search", response_model=LocationConstraintsReport)
async def search_parallel_constraints(
    venue_name: str = "성수 루프탑 글래스하우스 스튜디오",
    address: str = "서울시 성동구 성수이로",
    council_area: str = "서울시 성동구",
    language: str = "ko",
):
    return await search_location_constraints_with_parallel(
        venue_name=venue_name,
        address=address,
        council_area=council_area,
        language="en" if language == "en" else "ko",
    )

@app.get("/api/demo", response_model=SceneInput)
def get_demo_scene():
    return SceneInput()
