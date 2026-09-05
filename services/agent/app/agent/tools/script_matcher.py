"""
Script → location matching.

Every venue this returns comes out of app.catalog, i.e. a real hourplace.co.kr
listing. Nothing here invents a venue name, a price or a spec: when the catalog
is empty the response says so instead of fabricating recommendations.
"""
import os
import asyncio
import re
import json
import logging
from typing import List, Dict, Any, Optional

from pydantic import BaseModel

from app import catalog
from app.config import settings
from app.gemini_models import TEXT_MODELS, try_models
from app.models.korean_locations import KoreanLocation
from app.agent.tools.location_localizer import localize_locations

logger = logging.getLogger(__name__)


class SceneMatchResult(BaseModel):
    scene_number: str
    scene_title: str
    scene_summary: str
    mood: str
    time_of_day: str
    required_space_type: str
    recommended_location_ids: List[str]
    ai_recommendation_reason: str
    primary_location: KoreanLocation
    alternative_location: Optional[KoreanLocation] = None


class ScriptAnalysisResponse(BaseModel):
    project_title: str
    total_scenes_detected: int
    scenes: List[SceneMatchResult]
    # A short name for the conversation, derived from the screenplay. Asked for
    # in the same generate_content call as the scene matching — naming a thread
    # is not worth a second round trip to the model.
    thread_title: str = ""
    overall_production_advice: str


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    suggested_location_ids: Optional[List[str]] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    current_scene_context: Optional[str] = None
    selected_location_id: Optional[str] = None
    # A passage the user highlighted in their own script and attached to the
    # question. Quoted to the model verbatim so the recommendation is about
    # that passage rather than about the whole screenplay.
    script_excerpt: Optional[str] = None
    language: str = "ko"


class ChatResponse(BaseModel):
    reply: str
    suggested_locations: List[KoreanLocation] = []
    applied_filter_summary: Optional[str] = None
    # Which model actually answered. Surfaced in the UI: without it there is no
    # way for a user to tell a real answer from a canned one, and this endpoint
    # used to be canned.
    model: str = ""


DEFAULT_KOREAN_SCRIPT = """[씬 14: 실내 다이닝룸 - 일몰]
엘레나와 마커스가 묵직한 원목 식탁을 사이에 두고 마주 앉아 있다.
엘레나 뒤편 서쪽 창문으로 쏟아지는 눈부신 황금빛 일몰 햇살이 식어가는 찻잔의 김을 비춘다.
카메라는 두 인물을 담는 와이드 투샷(Wide Two-shot)으로 시작하여, 둘 사이의 팽팽한 침묵 속으로 서서히 앞으로 돌리-인(Dolly-in)한다.

[씬 15: 야외 깊은 숲속 오솔길 - 황혼에서 밤]
엘레나가 문을 박차고 나와 안개 낀 숲길로 뛰어 들어간다.
키 큰 잣나무들 사이로 푸른빛 박명이 깔리고, 멀리서 바스락거리는 추격자의 발소리가 들려온다.
카메라는 핸드헬드로 흔들리며 나무 사이를 가로지르는 엘레나의 긴박한 호흡을 따라간다."""

EMPTY_CATALOG_ADVICE = (
    "카탈로그가 비어 있어 매칭할 실제 매물이 없습니다. "
    "탐색 탭의 '실제 매물 수집' 버튼을 눌러 아워플레이스 매물을 먼저 수집해주세요."
)


# ── Catalog helpers ─────────────────────────────────────────────────────────
def _diverse_sample(limit: int = 60) -> List[KoreanLocation]:
    """A spread across categories/regions so Gemini sees the range of the catalog,
    not just the first N listings from one district."""
    pool = catalog.all_locations()
    buckets: Dict[str, List[KoreanLocation]] = {}
    for loc in pool:
        buckets.setdefault(f"{loc.category}|{loc.region_category}", []).append(loc)

    out: List[KoreanLocation] = []
    round_i = 0
    while len(out) < limit:
        added = False
        for key in sorted(buckets):
            group = buckets[key]
            if round_i < len(group):
                out.append(group[round_i])
                added = True
                if len(out) >= limit:
                    break
        if not added:
            break
        round_i += 1
    return out


def _summarize_for_prompt(locations: List[KoreanLocation], language: str = "ko") -> str:
    if language == "en":
        return "\n".join(
            f"- ID: {l.id} | source name: {l.name[:50]} | source category: {l.category} | "
            f"source region: {l.region} | "
            f"{('KRW ' + format(l.price_per_hour, ',') + '/h') if l.price_per_hour else 'price unknown'} | "
            f"{l.specs.area_sqm} sqm | source window note: {l.specs.window_direction} | "
            f"source tags: {', '.join(l.tags[:4])}"
            for l in locations
        )
    return "\n".join(
        f"- ID: {l.id} | {l.name[:50]} | {l.category} | {l.region} | "
        f"{('₩' + format(l.price_per_hour, ',') + '/h') if l.price_per_hour else '가격미상'} | "
        f"{l.specs.area_pyeong}평 | 창: {l.specs.window_direction} | 태그: {', '.join(l.tags[:4])}"
        for l in locations
    )


def _pick(predicate, limit: int = 2) -> List[KoreanLocation]:
    hits = [l for l in catalog.all_locations() if predicate(l)]
    return hits[:limit] if hits else catalog.all_locations()[:limit]


# ── Script analysis ─────────────────────────────────────────────────────────
SCENE_HEADER_RE = re.compile(r"\[?\s*(씬|S#|SCENE)\s*([0-9]+)\s*[:\]]?\s*([^\n\]]*)", re.IGNORECASE)


def _split_scenes(script_text: str) -> List[Dict[str, str]]:
    """Split a Korean screenplay on its scene headers, keeping each scene's body."""
    matches = list(SCENE_HEADER_RE.finditer(script_text))
    if not matches:
        return [{"number": "씬 1", "title": "전체 씬", "body": script_text.strip()}]
    scenes = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(script_text)
        scenes.append({
            "number": f"씬 {m.group(2)}",
            "title": (m.group(3) or "").strip(" -–]") or f"씬 {m.group(2)}",
            "body": script_text[m.end():end].strip(),
        })
    return scenes


def _infer_scene_needs(body: str, title: str) -> Dict[str, Any]:
    blob = f"{title} {body}"
    outdoor = any(k in blob for k in ("야외", "숲", "산", "바다", "강", "공원", "거리", "옥상", "루프탑"))
    hanok = any(k in blob for k in ("한옥", "고택", "사극", "대청", "마당"))
    night = any(k in blob for k in ("밤", "야간", "새벽", "심야"))
    sunset = any(k in blob for k in ("일몰", "노을", "황혼", "골든아워", "석양"))
    west_window = any(k in blob for k in ("서쪽 창", "서향", "일몰 햇살", "역광"))

    if hanok:
        category = "전통 한옥"
    elif outdoor:
        category = "자연/야외"
    elif any(k in blob for k in ("카페", "갤러리", "전시")):
        category = "카페/갤러리"
    elif any(k in blob for k in ("집", "거실", "다이닝", "주방", "침실", "하우스")):
        category = "럭셔리 하우스"
    else:
        category = "모던 스튜디오"

    if night:
        time_of_day = "야간 (인공조명 필수)"
    elif sunset:
        time_of_day = "일몰 / 골든아워"
    else:
        time_of_day = "주간"

    needs = []
    if west_window or sunset:
        needs.append("서향 자연광 창")
    if outdoor:
        needs.append("야외 부지")
    if any(k in blob for k in ("돌리", "레일", "트랙")):
        needs.append("돌리 레일 동선 확보")
    if any(k in blob for k in ("와이드", "투샷", "롱테이크")):
        needs.append("넓은 촬영 거리")

    return {
        "category": category,
        "time_of_day": time_of_day,
        "required_space_type": " + ".join(needs) or f"{category} 성격의 실내 공간",
        "wants_west_window": west_window or sunset,
        "outdoor": outdoor,
        "night": night,
    }


def _match_scene(needs: Dict[str, Any]) -> List[KoreanLocation]:
    cat = needs["category"]

    def scorer(loc: KoreanLocation) -> int:
        score = 0
        if loc.category == cat:
            score += 5
        if needs["wants_west_window"] and ("서향" in loc.specs.window_direction or "오후" in loc.specs.natural_light_type):
            score += 4
        if needs["wants_west_window"] and "자연광" in loc.specs.window_direction:
            score += 2
        if needs["night"] and "암막" in loc.specs.window_direction:
            score += 3
        if loc.specs.area_pyeong >= 30:
            score += 1
        if loc.images:
            score += 1
        if loc.rating >= 4.5:
            score += 1
        return score

    ranked = sorted(catalog.all_locations(), key=scorer, reverse=True)
    return ranked[:2]


def _reason_for(loc: KoreanLocation, needs: Dict[str, Any]) -> str:
    bits = [f"{loc.region}의 {loc.category}"]
    if loc.specs.area_pyeong:
        bits.append(f"{loc.specs.area_pyeong}평")
    if loc.specs.ceiling_height_m:
        bits.append(f"천고 {loc.specs.ceiling_height_m}m")
    if "확인 필요" not in loc.specs.window_direction:
        bits.append(f"창 방향 {loc.specs.window_direction}")
    if loc.price_per_hour:
        bits.append(f"시간당 ₩{loc.price_per_hour:,}")
    head = ", ".join(bits)
    tail = (
        "일몰 역광이 필요한 씬이므로 상세 페이지의 채광 시뮬레이터로 촬영일 기준 직사광 시간대를 먼저 확인하세요."
        if needs["wants_west_window"]
        else "상세 페이지의 채광 시뮬레이터에서 촬영 예정 시간대의 빛을 확인할 수 있습니다."
    )
    return f"{head}. {tail}"


async def analyze_script_and_match_locations(
    script_text: str,
    project_title: str = "마지막 일몰 (The Last Sunset)",
    language: str = "ko",
) -> ScriptAnalysisResponse:
    if catalog.size() == 0:
        return ScriptAnalysisResponse(
            project_title=project_title,
            total_scenes_detected=0,
            scenes=[],
            overall_production_advice=EMPTY_CATALOG_ADVICE,
        )

    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    sample = _diverse_sample(60)

    if api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            if language == "en":
                prompt = f"""You are StageSight, an AI location supervisor for film productions in South Korea.
Analyse the screenplay scene by scene, identify spatial and lighting requirements, and select first and second choices only from the real locations below.

[IMPORTANT]
Every recommended_location_id must exist in the supplied list. Never invent a location.

[REAL BOOKABLE LOCATIONS]
{_summarize_for_prompt(sample, "en")}

[SCREENPLAY]
{script_text[:4000]}

Respond only with JSON:
{{
  "project_title": "{project_title}",
  "thread_title": "A concise English noun phrase describing the screenplay",
  "scenes": [{{
    "scene_number": "Scene 14",
    "scene_title": "Interior dining room - sunset",
    "scene_summary": "Scene summary",
    "mood": "Mood",
    "time_of_day": "Sunset / golden hour (17:30)",
    "required_space_type": "Required spatial conditions",
    "recommended_location_ids": ["<real ID from list>", "<real ID from list>"],
    "ai_recommendation_reason": "Why the space fits the scene"
  }}],
  "overall_production_advice": "Advice on scheduling and production movement"
}}"""
            else:
                prompt = f"""당신은 대한민국 영화 프로덕션의 로케이션 슈퍼바이저 AI, StageSight입니다.
아래 각본을 씬 단위로 분석해 각 씬의 공간적·조명적 요구사항을 파악하고,
제공된 실제 로케이션 목록 중에서만 1순위와 2순위를 골라주세요.

[중요] recommended_location_ids 에는 반드시 아래 목록에 실제로 존재하는 ID만 사용하세요.
목록에 없는 장소를 지어내지 마세요.

[실제 대관 가능 로케이션 목록]
{_summarize_for_prompt(sample)}

[각본]
{script_text[:4000]}

JSON으로만 응답:
{{
  "project_title": "{project_title}",
  "thread_title": "이 각본이 무엇에 대한 것인지 12자 내외 한국어 명사구. 예: '숲속 추격 시퀀스', '한옥 다이닝 씬'",
  "scenes": [
    {{
      "scene_number": "씬 14",
      "scene_title": "실내 다이닝룸 - 일몰",
      "scene_summary": "씬 내용 요약",
      "mood": "분위기",
      "time_of_day": "일몰 / 골든아워 (17:30)",
      "required_space_type": "필요한 공간 조건",
      "recommended_location_ids": ["<목록의 실제 ID>", "<목록의 실제 ID>"],
      "ai_recommendation_reason": "왜 이 공간이 이 씬에 맞는지"
    }}
  ],
  "overall_production_advice": "회차 분리 및 동선 관련 조언"
}}"""
            response = try_models(
                TEXT_MODELS,
                lambda m: client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                ),
            )

            if response.text:
                data = json.loads(response.text.strip())
                parsed_scenes: List[SceneMatchResult] = []

                for s in data.get("scenes", []):
                    rec_ids = [i for i in (s.get("recommended_location_ids") or []) if catalog.by_id(i)]
                    if rec_ids:
                        primary = catalog.by_id(rec_ids[0])
                        alternative = catalog.by_id(rec_ids[1]) if len(rec_ids) > 1 else None
                    else:
                        # Gemini named something that isn't in the catalog — fall back to
                        # our own ranking rather than shipping a hallucinated venue.
                        needs = _infer_scene_needs(s.get("scene_summary", ""), s.get("scene_title", ""))
                        picks = _match_scene(needs)
                        primary = picks[0] if picks else None
                        alternative = picks[1] if len(picks) > 1 else None
                        rec_ids = [p.id for p in picks]

                    if not primary:
                        continue

                    parsed_scenes.append(SceneMatchResult(
                        scene_number=s.get("scene_number", "씬 1"),
                        scene_title=s.get("scene_title", "씬 제목"),
                        scene_summary=s.get("scene_summary", ""),
                        mood=s.get("mood", "드라마틱"),
                        time_of_day=s.get("time_of_day", "주간"),
                        required_space_type=s.get("required_space_type", "실내"),
                        recommended_location_ids=rec_ids,
                        ai_recommendation_reason=s.get("ai_recommendation_reason", ""),
                        primary_location=primary,
                        alternative_location=alternative,
                    ))

                if parsed_scenes:
                    if language == "en":
                        for scene in parsed_scenes:
                            translated = await localize_locations(
                                [x for x in (scene.primary_location, scene.alternative_location) if x],
                                "en",
                            )
                            scene.primary_location = translated[0]
                            scene.alternative_location = translated[1] if len(translated) > 1 else None
                    return ScriptAnalysisResponse(
                        project_title=data.get("project_title", project_title),
                        total_scenes_detected=len(parsed_scenes),
                        scenes=parsed_scenes,
                        overall_production_advice=data.get("overall_production_advice", ""),
                        thread_title=(data.get("thread_title") or "").strip()[:40],
                    )
        except Exception as e:
            logger.warning(f"Gemini script matching failed, using deterministic matcher: {e}")

    # Deterministic matcher — parses the script itself and ranks the real catalog.
    scenes: List[SceneMatchResult] = []
    for sc in _split_scenes(script_text)[:6]:
        needs = _infer_scene_needs(sc["body"], sc["title"])
        picks = _match_scene(needs)
        if not picks:
            continue
        scenes.append(SceneMatchResult(
            scene_number=sc["number"],
            scene_title=sc["title"],
            scene_summary=sc["body"][:180],
            mood=needs["time_of_day"],
            time_of_day=needs["time_of_day"],
            required_space_type=needs["required_space_type"],
            recommended_location_ids=[p.id for p in picks],
            ai_recommendation_reason=_reason_for(picks[0], needs),
            primary_location=picks[0],
            alternative_location=picks[1] if len(picks) > 1 else None,
        ))

    regions = {s.primary_location.region_category for s in scenes}
    advice = (
        f"{len(scenes)}개 씬이 {len(regions)}개 권역({', '.join(sorted(regions))})에 걸쳐 있습니다. "
        "권역별로 회차를 묶어 이동을 줄이고, 자연광이 필요한 씬은 각 매물 상세의 채광 시뮬레이터로 "
        "촬영일 기준 골든아워를 확인한 뒤 콜타임을 정하세요."
    ) if scenes else EMPTY_CATALOG_ADVICE

    if language == "en":
        for scene in scenes:
            translated = await localize_locations(
                [x for x in (scene.primary_location, scene.alternative_location) if x], "en"
            )
            scene.primary_location = translated[0]
            scene.alternative_location = translated[1] if len(translated) > 1 else None

    return ScriptAnalysisResponse(
        project_title=project_title,
        total_scenes_detected=len(scenes),
        scenes=scenes,
        overall_production_advice=advice,
    )


# ── Conversational scouting ─────────────────────────────────────────────────
def _name_list(locs: List[KoreanLocation]) -> str:
    return ", ".join(f"**{l.name[:34]}**" for l in locs[:2]) or "조건에 맞는 매물"


async def chat_with_script_ai(req: ChatRequest) -> ChatResponse:
    """A real conversation with Gemini over the real catalog.

    This used to be a keyword router — `"한옥" in question` picked a category and
    a fixed sentence was returned. It looked like an assistant and was not one,
    and a user could not tell the difference. It now calls the model, and when
    the model cannot be reached it says so rather than falling back to canned
    replies. The only thing still enforced in code is that every listing named
    in a reply exists in the catalog: ids that come back are looked up, and
    anything invented is dropped.
    """
    if catalog.size() == 0:
        return ChatResponse(
            reply=("The real-location catalogue is empty, so no recommendation can be made." if req.language == "en" else EMPTY_CATALOG_ADVICE),
            suggested_locations=[],
        )

    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    sample = _diverse_sample(60)

    # The whole thread, so follow-ups ("그럼 더 싼 곳은?") resolve against what
    # was already recommended.
    history = "\n".join(
        f"{('User' if m.role == 'user' else 'Assistant') if req.language == 'en' else ('사용자' if m.role == 'user' else 'AI')}: {m.content[:600]}"
        for m in req.messages[-12:]
    )

    excerpt_block = ""
    if req.script_excerpt:
        excerpt_block = (
            "\n[SCREENPLAY EXCERPT SELECTED BY THE USER]\n"
            f"\"\"\"{req.script_excerpt[:1500]}\"\"\"\n"
            "First identify its spatial, lighting and movement needs, then recommend matching locations.\n"
        ) if req.language == "en" else (
            "\n[사용자가 자기 각본에서 직접 지목한 대목]\n"
            f"\"\"\"{req.script_excerpt[:1500]}\"\"\"\n"
            "이 대목이 요구하는 공간·조명·동선 조건을 먼저 읽어낸 뒤, 그 조건에 맞는 장소를 고르세요.\n"
        )

    scene_block = (
        f"\n[PREVIOUS SCREENPLAY ANALYSIS]\n{req.current_scene_context}\n"
        if req.language == "en" else f"\n[직전 각본 분석 결과]\n{req.current_scene_context}\n"
    ) if req.current_scene_context else ""

    if req.language == "en":
        prompt = f"""You are StageSight, an AI location supervisor for film productions in South Korea.
Recommend locations only from the real bookable catalogue below while conversing with the user.

[ABSOLUTE RULES]
- Every recommended_location_id must exist in the supplied list. Never invent a location.
- If nothing matches, say so and suggest which constraints could be relaxed.
- Use prices, area and window orientation only when present in the catalogue; state when they are unknown.
- The reply and filter summary must contain English only: translate or romanise every Korean venue name, region, category and specification you mention. Never output Hangul.
- Express area in square metres, not pyeong.

[REAL BOOKABLE LOCATIONS]
{_summarize_for_prompt(sample, "en")}
{scene_block}{excerpt_block}
[CONVERSATION]
{history}

Answer the user's latest question in English. Respond only with JSON:
{{
  "reply": "A conversational answer connecting each recommendation to the scene requirements",
  "recommended_location_ids": ["<real ID from list>"],
  "filter_summary": "One-line summary of applied filters, or an empty string"
}}"""
    else:
        prompt = f"""당신은 대한민국 영화 프로덕션의 로케이션 슈퍼바이저 AI, StageSight입니다.
사용자와 대화하며 아래 '실제 대관 가능 목록'에서만 장소를 추천합니다.

[절대 규칙]
- recommended_location_ids 에는 아래 목록에 실제로 존재하는 ID만 쓰세요. 없는 장소를 지어내지 마세요.
- 목록에 조건에 맞는 곳이 없으면 억지로 고르지 말고, 없다고 말하고 조건 완화를 제안하세요.
- 가격·평수·창 방향 같은 수치는 목록에 있는 값만 쓰고, 모르면 모른다고 하세요.

[실제 대관 가능 로케이션 목록]
{_summarize_for_prompt(sample)}
{scene_block}{excerpt_block}
[지금까지의 대화]
{history}

사용자의 마지막 질문에 한국어로 답하세요. JSON으로만 응답:
{{
  "reply": "대화체 답변. 왜 이 장소들인지 근거를 씬 요구조건과 연결해 설명",
  "recommended_location_ids": ["<목록의 실제 ID>"],
  "filter_summary": "적용한 조건 한 줄 요약 (없으면 빈 문자열)"
}}"""

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    used = {"model": ""}

    def _call(m: str):
        used["model"] = m
        return client.models.generate_content(
            model=m,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )

    # The SDK call is synchronous and takes seconds; awaited directly it would
    # block every other request on the event loop.
    response = await asyncio.to_thread(lambda: try_models(TEXT_MODELS, _call))
    if not response.text:
        raise RuntimeError("CHAT_EMPTY_RESPONSE")

    data = json.loads(response.text.strip())

    # The evidence is Korean. Models occasionally copy a source brand name or
    # unit into an otherwise English answer, so repair that language boundary
    # once before the response reaches the English UI.
    if req.language == "en":
        reply_text = str(data.get("reply") or "")
        filter_text = str(data.get("filter_summary") or "")
        if re.search(r"[가-힣]", reply_text + filter_text):
            repair_prompt = f"""Translate every Korean word in this JSON into natural English or romanised English.
Preserve IDs, prices and factual meaning exactly. Convert pyeong areas to square metres using 1 pyeong = 3.3058 sqm. Return only JSON with the same two keys and no Hangul.
{json.dumps({"reply": reply_text, "filter_summary": filter_text}, ensure_ascii=False)}"""
            repaired = await asyncio.to_thread(
                lambda: try_models(
                    TEXT_MODELS,
                    lambda model: client.models.generate_content(
                        model=model,
                        contents=repair_prompt,
                        config=types.GenerateContentConfig(response_mime_type="application/json"),
                    ),
                )
            )
            repaired_data = json.loads(repaired.text or "{}")
            data["reply"] = repaired_data.get("reply", reply_text)
            data["filter_summary"] = repaired_data.get("filter_summary", filter_text)

    # Ids are validated, not trusted. A hallucinated venue is the one failure
    # mode this product cannot ship.
    matched: List[KoreanLocation] = []
    for lid in (data.get("recommended_location_ids") or [])[:6]:
        loc = catalog.by_id(lid)
        if loc and all(loc.id != m.id for m in matched):
            matched.append(loc)

    if req.language == "en":
        matched = await localize_locations(matched, "en")

    reply = (data.get("reply") or "").strip()
    if not reply:
        raise RuntimeError("CHAT_EMPTY_RESPONSE")

    return ChatResponse(
        reply=reply,
        suggested_locations=matched,
        applied_filter_summary=(data.get("filter_summary") or None),
        model=used["model"],
    )
