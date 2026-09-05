"""
placehub.co.kr — second live rental platform.

Why this one, and why it is small:

  * robots.txt is `User-Agent: * / Allow: /`, with `/api`, `/admin`, `/my` and
    `/booking` disallowed. Listing pages are explicitly permitted.
  * Every listing page publishes a schema.org `Product` block — name, category,
    region, price in KRW, and the real photo URLs. That is data the site
    publishes *for machines to read*, so it is parsed rather than scraped out of
    markup that was never meant to be read.
  * The listing index is rendered client-side; the only server-side way to
    enumerate everything is `/api`, which robots disallows. So enumeration uses
    the allowed pages only and yields ~12 listings rather than the full
    catalogue. That is the honest ceiling under robots, and it is not raised by
    calling this a hackathon — the same line was held against hourplace's richer
    `api2` endpoint.

Rights are `ROBOTS_ALLOWED`: crawling is permitted, republication is not
separately licensed, and PlaceHub's terms require prior consent for secondary
use of their content. Fine for a non-commercial hackathon demo that links back;
before any public deployment this needs to become `PARTNER_APPROVED`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.models.korean_locations import KoreanLocation, LocationSpec  # noqa: E402
from app.models.schemas import ParallelCitation  # noqa: E402

from .base import Kind, RawListing, Rights  # noqa: E402

SITE = "https://placehub.co.kr"
UA = {"User-Agent": "Claude-User"}

# Allowed entry points. `/api` is disallowed by robots and deliberately unused,
# which is why this provider sees a slice rather than the whole catalogue.
INDEX_PATHS = ["/", "/salehub"] + [
    f"/categories/{s}"
    for s in ("studio", "party-room", "practice-room", "meeting-room", "cafe",
              "gallery", "dance", "photo", "house", "rooftop")
]

_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_PLACE = re.compile(r"/places/(\d+)")

# PlaceHub's own category vocabulary onto StageSight's.
CATEGORY_MAP = {
    "스튜디오": "모던 스튜디오",
    "촬영스튜디오": "모던 스튜디오",
    "파티룸": "럭셔리 하우스",
    "카페": "카페/갤러리",
    "갤러리": "카페/갤러리",
    "공연장": "빈티지/창고",
    "연습실": "모던 스튜디오",
    "회의실": "모던 스튜디오",
}

REGION_PREFIXES = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
                   "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]


def _ld_blocks(html: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in _LD.findall(html):
        try:
            d = json.loads(raw.strip())
        except Exception:
            continue
        out.extend(d if isinstance(d, list) else [d])
    return out


def _ceiling_from_text(text: str) -> float:
    """Ceiling height is not a schema.org field, but hosts state it in prose
    ("4.8m의 높은 층고"). Read only an explicit figure — never estimate one,
    because this feeds the lens and lighting maths."""
    m = re.search(r"(?:층고|천장고|천고)\s*(?:는|가)?\s*(\d+(?:\.\d+)?)\s*m", text)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*m\s*(?:의\s*)?(?:높은\s*)?(?:층고|천장고|천고)", text)
    if not m:
        return 0.0
    v = float(m.group(1))
    return v if 1.8 <= v <= 20 else 0.0


def _area_from_text(text: str) -> tuple[float, float]:
    """(sqm, pyeong) if the host states one. 평 and ㎡ both appear."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*평", text)
    if m:
        p = float(m.group(1))
        if 1 <= p <= 3000:
            return round(p * 3.3058, 1), p
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:㎡|m2|제곱미터)", text)
    if m:
        s = float(m.group(1))
        if 3 <= s <= 10000:
            return s, round(s / 3.3058, 1)
    return 0.0, 0.0


def _natural_light(text: str) -> tuple[str, str]:
    """(window_direction, natural_light_type) — only what the host actually
    wrote. Window direction drives the solar engine, so a guessed bearing would
    produce confident nonsense; unknown stays "확인 필요"."""
    direction = "확인 필요"
    for kor, label in (("남향", "남향"), ("북향", "북향"), ("동향", "동향"), ("서향", "서향"),
                       ("남동향", "남동향"), ("남서향", "남서향")):
        if kor in text:
            direction = label
            break
    if "암막" in text and direction == "확인 필요":
        direction = "암막 (자연광 차단 가능)"
    light = "확인 필요"
    if any(k in text for k in ("통창", "자연광", "채광", "햇살", "햇빛")):
        light = "자연광 우수"
    elif "무창" in text or "지하" in text:
        light = "자연광 없음"
    return direction, light


class PlacehubProvider:
    name = "placehub"
    id_prefix = "ph2_"          # "ph_" already belongs to hourplace
    rights = Rights.ROBOTS_ALLOWED
    default_kind = Kind.BOOKABLE
    label = "플레이스허브"
    site_url = SITE

    def __init__(self) -> None:
        self._html: Dict[str, str] = {}

    async def prepare(self, client: Any) -> None:
        self._html.clear()

    async def discover_ids(self, client: Any) -> List[str]:
        import asyncio

        ids: set[str] = set()
        for path in INDEX_PATHS:
            try:
                r = await client.get(SITE + path, headers=UA, timeout=25.0)
            except Exception:
                continue
            if r.status_code == 200:
                ids |= set(_PLACE.findall(r.text))
            await asyncio.sleep(0.3)   # a guest's pace
        return sorted(ids, key=int)

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        r = await client.get(f"{SITE}/places/{source_id}", headers=UA, timeout=25.0)
        if r.status_code != 200:
            return None
        product = next(
            (b for b in _ld_blocks(r.text) if b.get("@type") == "Product"), None
        )
        if not product:
            return None
        return RawListing(source_id=source_id, payload=product)

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        p = raw.payload
        name = (p.get("name") or "").strip()
        images = [u for u in (p.get("image") or []) if isinstance(u, str)]
        # Same rule as every other source: no photo, no listing.
        if not name or not images:
            return None

        desc = (p.get("description") or "").strip()
        area_served = (p.get("areaServed") or "").strip()
        offers = p.get("offers") or {}
        try:
            price = int(float(offers.get("price") or 0))
        except (TypeError, ValueError):
            price = 0

        bucket = next((r for r in REGION_PREFIXES if area_served.startswith(r)), "기타")
        region = area_served or "지역 확인 필요"
        sqm, pyeong = _area_from_text(f"{name} {desc}")
        direction, light = _natural_light(desc)

        return KoreanLocation(
            id=f"{self.id_prefix}{raw.source_id}",
            name=name,
            tagline=desc.split("\n")[0][:90],
            region=region,
            region_category=bucket,
            category=CATEGORY_MAP.get(p.get("category", ""), "모던 스튜디오"),
            price_per_hour=price,
            # PlaceHub quotes an hourly rate; a daily figure is not published and
            # is not extrapolated from it.
            price_per_day=0,
            min_hours=0,
            rating=0.0,
            review_count=0,
            images=images[:12],
            specs=LocationSpec(
                area_sqm=sqm,
                area_pyeong=pyeong,
                ceiling_height_m=_ceiling_from_text(desc),
                window_direction=direction,
                natural_light_type=light,
                golden_hour_window="확인 필요",
                power_capacity="확인 필요",
                parking_spots=0,
                has_freight_elevator=False,
                sound_recording_quality="확인 필요",
            ),
            tags=["플레이스허브", p.get("category", "")][:2],
            permit_summary="플레이스허브 등록 매물입니다. 촬영 조건과 허가는 원본 페이지에서 확인하세요.",
            citations=[
                ParallelCitation(
                    title=f"{name} — 플레이스허브",
                    url=f"{SITE}/places/{raw.source_id}",
                    excerpt=f"{area_served} · {desc}"[:200],
                    source_type="플레이스허브 (placehub.co.kr) 실시간 수집",
                    retrieval_timestamp="",
                    confidence_score=0.95,
                    verification_status="LIVE",
                )
            ],
        )

    def source_url(self, source_id: str) -> str:
        return f"{SITE}/places/{source_id}"
