"""
국가유산청 (Korea Heritage Service) — palaces, fortresses, hanok, temples.

The one source on the roadmap that needs **no key and no download**. Its open
API answers unauthenticated, returns coordinates and a hosted photograph per
record, and covers exactly what a period drama or a historical shoot needs and
hourplace cannot supply: 숭례문, 경복궁, 하회마을, 산성, 서원, 고택.

    GET /cha/SearchKindOpenapiList.do?ccbaCtcd=<region>   → ids per region
    GET /cha/SearchKindOpenapiDt.do?ccbaKdcd&ccbaAsno&ccbaCtcd → detail

Verified against the live service rather than a spec: 서울 alone returns 2,322
records, and 서울 숭례문 comes back with latitude/longitude, an `imageUrl` on
khs.go.kr, and a full 설명.

These are `reference`, and emphatically so. A national treasure is a real place
worth scouting, but filming on protected heritage land needs a permit from the
managing authority under the 국가유산보호법 — it is not something anyone books
by the hour. The permit_summary says so on every row.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.models.korean_locations import KoreanLocation, LocationSpec  # noqa: E402
from app.models.schemas import ParallelCitation  # noqa: E402

from .base import Kind, RawListing, Rights  # noqa: E402

BASE = "https://www.khs.go.kr/cha"
UA = {"User-Agent": "Claude-User"}

# ccbaCtcd → region bucket, matching the rest of the catalogue's vocabulary.
REGIONS = {
    "11": "서울", "21": "부산", "22": "인천", "23": "대전", "24": "대구",
    "25": "광주", "26": "울산", "45": "세종", "31": "경기", "32": "강원",
    "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북",
    "38": "경남", "50": "제주",
}

# Heritage kinds worth scouting. Movable property (국보 유물, 무형유산) is a
# museum object, not a location, and is skipped.
SCOUTABLE_KINDS = {"11", "12", "13", "14", "15", "16", "18", "79"}

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>(?:\s*<!\[CDATA\[)?(.*?)(?:\]\]>\s*)?</{name}>", xml, re.S)
    return (m.group(1) or "").strip() if m else ""


class HeritageProvider:
    name = "heritage"
    id_prefix = "kh_"
    rights = Rights.PUBLIC_OPEN_DATA
    default_kind = Kind.REFERENCE
    label = "국가유산청"
    site_url = "https://www.khs.go.kr"

    def __init__(self, per_region: int = 100) -> None:
        self.per_region = per_region
        self._index: Dict[str, Dict[str, str]] = {}
        self.unavailable_reason: Optional[str] = None

    async def prepare(self, client: Any) -> None:
        import asyncio

        self._index.clear()
        for ctcd, region in REGIONS.items():
            try:
                r = await client.get(
                    f"{BASE}/SearchKindOpenapiList.do", headers=UA, timeout=30.0,
                    params={"ccbaCtcd": ctcd, "pageIndex": 1, "pageUnit": self.per_region},
                )
            except Exception:
                continue
            if r.status_code != 200:
                continue
            for block in _ITEM.findall(r.text):
                kd, asno = _tag(block, "ccbaKdcd"), _tag(block, "ccbaAsno")
                if not kd or not asno or kd not in SCOUTABLE_KINDS:
                    continue
                self._index[f"{kd}_{asno}_{ctcd}"] = {
                    "kd": kd, "asno": asno, "ctcd": ctcd, "region": region,
                    "name": _tag(block, "ccbaMnm1"),
                }
            await asyncio.sleep(0.15)   # a guest's pace

        if not self._index:
            self.unavailable_reason = "국가유산청 목록을 가져오지 못했습니다."

    async def discover_ids(self, client: Any) -> List[str]:
        return list(self._index.keys())

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        meta = self._index.get(source_id)
        if not meta:
            return None
        r = await client.get(
            f"{BASE}/SearchKindOpenapiDt.do", headers=UA, timeout=30.0,
            params={"ccbaKdcd": meta["kd"], "ccbaAsno": meta["asno"], "ccbaCtcd": meta["ctcd"]},
        )
        if r.status_code != 200:
            return None
        return RawListing(source_id=source_id, payload={"xml": r.text, "meta": meta})

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        xml = raw.payload["xml"]
        meta = raw.payload["meta"]

        name = _tag(xml, "ccbaMnm1") or meta.get("name", "")
        image = _tag(xml, "imageUrl")
        # Same rule as every source: no photograph, no row.
        if not name or not image:
            return None

        address = _tag(xml, "ccbaLcad")
        content = _tag(xml, "content")
        kind_name = _tag(xml, "ccmaName")          # 국보 / 보물 / 사적 …
        region = meta["region"]

        def _f(t: str) -> Optional[float]:
            try:
                v = float(_tag(xml, t) or 0)
                return v or None
            except ValueError:
                return None

        # A hanok/temple reads as 전통 한옥; a fortress or site is 자연/야외.
        category = "전통 한옥" if any(
            k in f"{name}{kind_name}" for k in ("가옥", "고택", "서원", "향교", "사찰", "종택", "재실")
        ) else "자연/야외"

        parts = address.split()
        display = f"{region} {parts[1]}" if len(parts) > 1 else region

        return KoreanLocation(
            id=f"{self.id_prefix}{raw.source_id}",
            name=name,
            tagline=(content[:88] or f"{kind_name} · {address}"[:88]),
            region=display,
            region_category=region,
            category=category,
            price_per_hour=0,
            price_per_day=0,
            min_hours=0,
            rating=0.0,
            review_count=0,
            images=[image],
            specs=LocationSpec(
                area_sqm=0.0, area_pyeong=0.0, ceiling_height_m=0.0,
                window_direction="확인 필요", natural_light_type="확인 필요",
                golden_hour_window="확인 필요", power_capacity="확인 필요",
                parking_spots=0, has_freight_elevator=False,
                sound_recording_quality="확인 필요",
            ),
            tags=["국가유산청", kind_name or "국가유산"],
            permit_summary=(
                f"{kind_name or '국가유산'} 지정 구역입니다. 촬영에는 관리 주체의 허가가 "
                "필요하며, 국가유산보호법에 따라 별도 심의가 요구될 수 있습니다."
            ),
            citations=[
                ParallelCitation(
                    title=f"{name} — 국가유산청",
                    url=f"{BASE}/SearchKindOpenapiDt.do?ccbaKdcd={meta['kd']}"
                        f"&ccbaAsno={meta['asno']}&ccbaCtcd={meta['ctcd']}",
                    excerpt=f"{address} · {content}"[:200],
                    source_type="국가유산청 국가유산 정보 오픈API (인증키 불요)",
                    retrieval_timestamp="",
                    confidence_score=0.95,
                    verification_status="PUBLIC_RECORD",
                )
            ],
            listing_kind=Kind.REFERENCE,
            latitude=_f("latitude"),
            longitude=_f("longitude"),
        )

    def source_url(self, source_id: str) -> str:
        m = self._index.get(source_id, {})
        if not m:
            return self.site_url
        return (f"{BASE}/SearchKindOpenapiDt.do?ccbaKdcd={m['kd']}"
                f"&ccbaAsno={m['asno']}&ccbaCtcd={m['ctcd']}")
