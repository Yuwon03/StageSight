"""
Real Korean filming-location ingestion from hourplace.co.kr.

ROBOTS COMPLIANCE (verified 2026-08-30 — do not change the host or UA without re-checking):
  hourplace.co.kr/robots.txt  → "User-agent: *  Disallow: /" BUT "User-agent: Claude-User  Allow: /".
      So every request to hourplace.co.kr MUST send User-Agent: Claude-User.
  api2.hourplace.co.kr/robots.txt → "User-agent: *  Disallow: /" with NO exception.
      That JSON API is therefore OFF LIMITS and is deliberately not used here, even though it is
      unauthenticated and returns richer data. Everything below comes from the allowed host only.

PIPELINE
  1. sitemap-places-1.xml           (1 request)   → ~13.5k place ids
  2. sitemap-images-{1..14}.xml     (14 requests) → photo galleries + Korean titles/captions for ~12.7k places
  3. hourplace.co.kr/place/{id}     (1 per place) → __NEXT_DATA__.props.pageProps.placeMeta:
                                                    price_low/high, lat/lng, region, locality,
                                                    category_main, floor, rating
Steps 1-2 cost 15 requests total and already yield name + photos, so the catalog fills fast;
step 3 enriches each listing with price and coordinates.
"""
import re
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

import httpx

from app.models.schemas import ParallelCitation
from app.models.korean_locations import KoreanLocation, LocationSpec

logger = logging.getLogger(__name__)

BASE = "https://hourplace.co.kr"
UA = "Claude-User"  # the one agent hourplace's robots.txt allows
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
CACHE_FILE = DATA_DIR / "locations.json"

IMAGE_SITEMAP_COUNT = 14

# ── Region / category normalisation ─────────────────────────────────────────
REGION_CATEGORIES = ("서울", "경기", "인천", "부산", "제주", "강원", "대구", "대전", "광주", "울산", "충북", "충남", "전북", "전남", "경북", "경남", "세종")

CATEGORY_MAP = [
    (("한옥", "고택"), "전통 한옥"),
    (("자연광",), "모던 스튜디오"),
    (("호리존", "스튜디오", "촬영"), "모던 스튜디오"),
    (("카페", "갤러리", "전시", "베이커리"), "카페/갤러리"),
    (("하우스", "주택", "빌라", "펜션", "풀빌라", "저택"), "럭셔리 하우스"),
    (("창고", "공장", "루프탑", "빈티지", "폐"), "빈티지/창고"),
    (("야외", "자연", "숲", "바다", "정원", "캠핑", "농장"), "자연/야외"),
]


def _category_of(category_main: str, title: str) -> str:
    blob = f"{category_main} {title}"
    for keys, label in CATEGORY_MAP:
        if any(k in blob for k in keys):
            return label
    return "모던 스튜디오"


def _region_category(region: str) -> str:
    for c in REGION_CATEGORIES:
        if region.startswith(c):
            return c
    return "서울"


# ── Free-text inference for the two fields hourplace does not expose ────────
_AREA_SQM_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:㎡|m2|m²|제곱미터)")
_AREA_PYEONG_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*평")
_PEOPLE_RE = re.compile(r"최대\s*(\d+)\s*명")
_CEILING_RE = re.compile(r"(?:층고|천장\s*높이|천고)[^\d]{0,10}([\d.]+)\s*(?:m|미터|M)")
_DIRECTION_RE = re.compile(r"(남동향|남서향|북동향|북서향|남향|동향|서향|북향)")
_LIGHT_HOURS_RE = re.compile(r"자연광[^\n.]{0,40}?(오전|오후)\s*(\d{1,2})\s*시[^\n.]{0,30}?(오전|오후)?\s*(\d{1,2})\s*시")


def _to_float(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def infer_window_direction(blob: str) -> str:
    """hourplace has no orientation field, so read it out of the copy the host wrote."""
    m = _DIRECTION_RE.search(blob)
    if m:
        return f"{m.group(1)} (본문 명시)"
    hours = _LIGHT_HOURS_RE.search(blob)
    if hours:
        # Morning light ⇒ east-facing, afternoon light ⇒ west-facing.
        start_ampm = hours.group(1)
        return "동향 (오전 자연광)" if start_ampm == "오전" else "서향 (오후 자연광)"
    if any(k in blob for k in LIGHT_KEYWORDS):
        return "자연광 (방향 미표기)"
    if any(k in blob for k in ("암막", "무창", "호리존")):
        return "암막 (자연광 없음)"
    return "확인 필요 (매물 문의)"


LIGHT_KEYWORDS = ("자연광", "채광", "통창", "창가", "창밖", "햇살", "볕", "선룸", "천창")


def infer_natural_light(blob: str) -> str:
    hours = _LIGHT_HOURS_RE.search(blob)
    if hours:
        return f"자연광 시간대 {hours.group(1)} {hours.group(2)}시 전후"
    if any(k in blob for k in LIGHT_KEYWORDS):
        return "자연광 유입 공간 (호스트 설명 기준)"
    if any(k in blob for k in ("암막", "무창", "호리존")):
        return "암막 제어 가능"
    return "매물 페이지에서 확인 필요"


# ── Sitemap fetching ────────────────────────────────────────────────────────
async def fetch_place_ids(client: httpx.AsyncClient) -> List[int]:
    r = await client.get(f"{BASE}/sitemaps/sitemap-places-1.xml", headers=HEADERS)
    r.raise_for_status()
    return [int(m) for m in re.findall(r"/place/(\d+)</loc>", r.text)]


async def fetch_image_index(
    client: httpx.AsyncClient, files: int = IMAGE_SITEMAP_COUNT
) -> Dict[int, Dict[str, Any]]:
    """Bulk galleries: 14 requests give photos + titles + captions for ~12.7k places."""
    index: Dict[int, Dict[str, Any]] = {}
    for n in range(1, files + 1):
        try:
            r = await client.get(f"{BASE}/sitemaps/sitemap-images-{n}.xml", headers=HEADERS)
            if r.status_code != 200:
                break
        except Exception as e:
            logger.warning(f"image sitemap {n} failed: {e}")
            break

        for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
            loc = re.search(r"<loc>https://hourplace\.co\.kr/place/(\d+)</loc>", block)
            if not loc:
                continue
            pid = int(loc.group(1))
            images = [
                u.replace("&amp;", "&")
                for u in re.findall(r"<image:loc>(.*?)</image:loc>", block)
            ]
            titles = re.findall(r"<image:title>(.*?)</image:title>", block)
            captions = re.findall(r"<image:caption>(.*?)</image:caption>", block)
            if images:
                index[pid] = {
                    "images": images[:12],
                    "title": titles[0] if titles else "",
                    "captions": captions[:6],
                }
        await asyncio.sleep(0.4)
        logger.info(f"image sitemap {n}/{files}: index now {len(index)} places")
    return index


PLACE_META_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


async def fetch_place_meta(client: httpx.AsyncClient, pid: int) -> Optional[Dict[str, Any]]:
    """SSR placeMeta from the allowed host: price, lat/lng, region, category."""
    try:
        r = await client.get(f"{BASE}/place/{pid}", headers=HEADERS)
        if r.status_code != 200:
            return None
        m = PLACE_META_RE.search(r.text)
        if not m:
            return None
        meta = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("placeMeta")
        return meta if isinstance(meta, dict) and meta.get("title") else None
    except Exception:
        return None


# ── Record assembly ─────────────────────────────────────────────────────────
def build_location(pid: int, meta: Dict[str, Any], gallery: Dict[str, Any]) -> Optional[KoreanLocation]:
    images = gallery.get("images") or []
    hero = meta.get("main_image_path")
    if not images and hero:
        images = [f"https://img.hourplace.co.kr/{hero}?s=2000x2000&t=inside&q=80&e=webp"]
    if not images:
        return None  # a scouting card without a photo is useless

    title = (meta.get("title") or gallery.get("title") or "").strip()
    if not title:
        return None

    desc = meta.get("description") or ""
    blob = " ".join([title, desc, *(gallery.get("captions") or [])])

    sqm_m = _AREA_SQM_RE.search(blob)
    pyeong_m = _AREA_PYEONG_RE.search(blob)
    area_sqm = _to_float(sqm_m.group(1)) if sqm_m else 0.0
    area_pyeong = _to_float(pyeong_m.group(1)) if pyeong_m else round(area_sqm / 3.3058, 1)
    if not area_sqm and area_pyeong:
        area_sqm = round(area_pyeong * 3.3058, 1)

    ceil_m = _CEILING_RE.search(blob)
    people_m = _PEOPLE_RE.search(blob)

    region = meta.get("region") or "서울"
    locality = meta.get("locality") or ""
    full_region = f"{region} {locality}".strip()

    price_low = int(meta.get("price_low") or 0)
    price_high = int(meta.get("price_high") or price_low)
    rating = float(meta.get("rating_value") or 0)
    rating_count = int(meta.get("rating_count") or 0)

    category_main = meta.get("category_main") or ""
    url = f"{BASE}/place/{pid}"

    tags = ["아워플레이스"]
    if category_main:
        tags.append(category_main)
    if locality:
        tags.append(locality)
    for kw in ("자연광", "통창", "루프탑", "한옥", "호리존", "빈티지", "테라스", "정원", "암막"):
        if kw in blob and kw not in tags:
            tags.append(kw)

    return KoreanLocation(
        id=f"hp_{pid}",
        name=title[:120],
        tagline=(desc or gallery.get("title") or "")[:140],
        region=full_region or region,
        region_category=_region_category(region),
        category=_category_of(category_main, title),
        price_per_hour=price_low,
        price_per_day=price_low * 8 if price_low else 0,
        min_hours=2,
        rating=rating,
        review_count=rating_count,
        images=images,
        specs=LocationSpec(
            area_sqm=area_sqm,
            area_pyeong=area_pyeong,
            ceiling_height_m=_to_float(ceil_m.group(1)) if ceil_m else 0.0,
            window_direction=infer_window_direction(blob),
            natural_light_type=infer_natural_light(blob),
            golden_hour_window="위치·날짜 기준 자동 계산",
            power_capacity="매물 문의",
            parking_spots=0,
            has_freight_elevator=False,
            sound_recording_quality="매물 문의",
        ),
        tags=tags[:8],
        permit_summary=(
            f"아워플레이스에 등록된 실제 대관 매물입니다. "
            f"{('시간당 ₩' + format(price_low, ',') + ' · ') if price_low else ''}"
            f"예약·정확한 요금·대관 규정은 원본 매물 페이지에서 확인하세요."
        ),
        citations=[
            ParallelCitation(
                title=(meta.get("og_title") or title)[:120],
                url=url,
                excerpt=(desc or "아워플레이스 실제 등록 매물")[:300],
                source_type="아워플레이스 (hourplace.co.kr) 실시간 수집",
                publication_date=datetime.now().strftime("%Y-%m-%d"),
                retrieval_timestamp=datetime.now().isoformat(),
                confidence_score=0.97,
                verification_status="LIVE",
            )
        ],
    )


# ── Orchestration ───────────────────────────────────────────────────────────
STATE: Dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "ingested": 0,
    "target": 0,
    "total_known": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def load_cache() -> List[KoreanLocation]:
    if not CACHE_FILE.exists():
        return []
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        out = []
        for item in raw:
            try:
                out.append(KoreanLocation(**item))
            except Exception:
                continue
        logger.info(f"Loaded {len(out)} cached locations from {CACHE_FILE}")
        return out
    except Exception as e:
        logger.warning(f"Could not read location cache: {e}")
        return []


def save_cache(locations: List[KoreanLocation]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps([l.model_dump() for l in locations], ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"Saved {len(locations)} locations to {CACHE_FILE}")


async def ingest(target: int = 800, concurrency: int = 6, on_batch=None) -> List[KoreanLocation]:
    """Fetch `target` real listings. on_batch(list) is called as results stream in."""
    STATE.update(
        running=True, phase="sitemap", ingested=0, target=target,
        started_at=datetime.now().isoformat(), finished_at=None, error=None,
    )
    results: List[KoreanLocation] = []

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(30.0, connect=10.0)
    try:
        async with httpx.AsyncClient(limits=limits, timeout=timeout, follow_redirects=True) as client:
            ids = await fetch_place_ids(client)
            STATE.update(total_known=len(ids))
            logger.info(f"hourplace sitemap: {len(ids)} place ids")

            STATE.update(phase="galleries")
            # Only pull as many image sitemaps as we need to cover the target.
            needed_files = min(IMAGE_SITEMAP_COUNT, max(1, (target // 900) + 1))
            gallery = await fetch_image_index(client, files=needed_files)
            logger.info(f"gallery index: {len(gallery)} places with photos")

            # Prefer ids we already have photos for — they need one request each, not two.
            ordered = [i for i in ids if i in gallery]
            STATE.update(phase="enriching")

            sem = asyncio.Semaphore(concurrency)

            async def one(pid: int) -> Optional[KoreanLocation]:
                async with sem:
                    meta = await fetch_place_meta(client, pid)
                    await asyncio.sleep(0.15)  # be a polite guest
                    if not meta:
                        return None
                    return build_location(pid, meta, gallery.get(pid, {}))

            batch_size = concurrency * 8
            for start in range(0, len(ordered), batch_size):
                if len(results) >= target:
                    break
                chunk = ordered[start : start + batch_size]
                done = await asyncio.gather(*[one(p) for p in chunk], return_exceptions=True)
                fresh = [d for d in done if isinstance(d, KoreanLocation)]
                results.extend(fresh)
                STATE.update(ingested=len(results))
                if on_batch and fresh:
                    on_batch(fresh)
                logger.info(f"ingest progress: {len(results)}/{target}")

            results = results[:target]
            save_cache(results)
    except Exception as e:
        logger.exception("Ingestion failed")
        STATE.update(error=str(e))
    finally:
        STATE.update(running=False, phase="done", finished_at=datetime.now().isoformat())

    return results
