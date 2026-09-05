"""
한국관광공사 관광정보 서비스 (data.go.kr 15101578) — the live public source.

This exists because the filming-location CSVs on the portal are file-only and
frozen: 서울특별시_드라마CF촬영장소 was last modified in 2021, and neither it nor
the 영상자료원 registers expose an open API, so a scout would be planning around
a five-year-old snapshot that nothing can refresh.

This one is different on exactly the two axes that matter:

  * **It updates.** A real open API (KorService2), so a crawl pass re-reads it
    like any other source instead of waiting for someone to re-download a file.
    `modifiedtime` comes back per row and is stored as `source_updated_at`.
  * **It has photographs.** `firstimage` / `firstimage2` are real hosted images,
    which is what makes these rows usable in the frame simulator at all — the
    filming-location CSVs carry none.

It covers 관광지 · 문화시설 · 자연 · 한옥 nationwide, which is precisely the gap
hourplace leaves: parks, coastlines, temples, heritage buildings. They are still
`reference`, not `bookable` — a public tourist site is a real place a production
can scout, but nobody has checked that it can be rented or filmed in, and permits
for public land are a separate process.

Needs `TOURAPI_KEY` (data.go.kr 활용신청, auto-approved). Without it this
provider imports nothing and says so.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.models.korean_locations import KoreanLocation, LocationSpec  # noqa: E402
from app.models.schemas import ParallelCitation  # noqa: E402

from .base import Kind, RawListing, Rights  # noqa: E402

BASE = "https://apis.data.go.kr/B551011/KorService2"
DATASET_PAGE = "https://www.data.go.kr/data/15101578/openapi.do"

# contentTypeId → StageSight category. Only the types worth scouting are pulled;
# restaurants and shopping are noise for a location search.
CONTENT_TYPES = {
    "12": ("관광지", "자연/야외"),
    "14": ("문화시설", "카페/갤러리"),
    "28": ("레포츠", "자연/야외"),
    "32": ("숙박", "럭셔리 하우스"),
}

# 법정동 시도 코드. Manual Ver 4.4 (2026-02-10) removed the old `areaCode`
# parameter and the areaCode2 operation with it; `lDongRegnCd` replaced them and
# uses different numbers entirely (부산 is 26 here, 6 under the old scheme).
# Fetched live from ldongCode2 rather than transcribed.
LDONG_REGIONS = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "30": "대전",
    "31": "울산", "41": "경기", "43": "충북", "44": "충남", "47": "경북",
    "48": "경남", "50": "제주", "51": "강원", "52": "전북", "12": "광주",
    "36110": "세종",
}

# cpyrhtDivCd — the row's own copyright type, per the manual:
#   Type1  출처 표시 시 이용 가능 (권장)
#   Type3  제1유형 + 변경금지
# Type3 photographs may not be altered, so they must never reach the AI frame
# simulator, which exists to alter them. Carried through to the record so the
# API and UI can enforce it rather than the crawler silently dropping the row.
NO_DERIVATIVES = "Type3"


class TourApiProvider:
    name = "tourapi"
    id_prefix = "ta_"
    rights = Rights.PUBLIC_OPEN_DATA
    default_kind = Kind.REFERENCE
    label = "한국관광공사"
    site_url = DATASET_PAGE

    def __init__(self, api_key: Optional[str] = None, per_area: int = 100) -> None:
        self.api_key = api_key or os.getenv("TOURAPI_KEY", "")
        self.per_area = per_area
        self._rows: Dict[str, Dict[str, Any]] = {}
        self.unavailable_reason: Optional[str] = None

    async def prepare(self, client: Any) -> None:
        self._rows.clear()
        if not self.api_key:
            self.unavailable_reason = (
                "TOURAPI_KEY 가 없습니다. data.go.kr/data/15101578/openapi.do 에서 "
                "활용신청(자동승인) 후 .env 에 TOURAPI_KEY 를 넣으세요."
            )
            return

        import asyncio

        for ldong, region in LDONG_REGIONS.items():
            for ctype in CONTENT_TYPES:
                # The service key is an *encoding* key (it arrives percent-
                # encoded, ending %3D%3D). Passing it through a param encoder
                # double-encodes the %, and the API answers
                # SERVICE_KEY_IS_NOT_REGISTERED_ERROR — so it goes into the URL
                # verbatim while everything else is encoded normally.
                query = (
                    f"serviceKey={self.api_key}"
                    "&MobileOS=ETC&MobileApp=StageSight&_type=json"
                    f"&numOfRows={self.per_area}&pageNo=1"
                    f"&lDongRegnCd={ldong}&contentTypeId={ctype}"
                    # arrange=Q is 수정일순 *and* guarantees a 대표 이미지 —
                    # the plain C sort returns rows with no photograph, which
                    # this catalogue drops anyway.
                    "&arrange=Q"
                )
                try:
                    r = await client.get(f"{BASE}/areaBasedList2?{query}", timeout=30.0)
                except Exception:
                    continue
                if r.status_code != 200 or "SERVICE_KEY_IS_NOT_REGISTERED" in r.text:
                    if not self.unavailable_reason:
                        self.unavailable_reason = (
                            f"TOURAPI 호출 실패 ({r.status_code}). 인증키가 Encoding 키인지, "
                            "활용신청 후 10분이 지났는지 확인하세요."
                        )
                        return
                    continue
                try:
                    body = r.json()["response"]["body"]
                    items = body.get("items") or {}
                    rows = items.get("item") or []
                except Exception:
                    continue
                for row in rows if isinstance(rows, list) else [rows]:
                    cid = str(row.get("contentid") or "")
                    # No photograph means the row cannot be scouted visually and
                    # is dropped, exactly as for the commercial sources.
                    if cid and (row.get("firstimage") or row.get("firstimage2")):
                        row["_region"] = region
                        self._rows[cid] = row
                await asyncio.sleep(0.1)

    async def discover_ids(self, client: Any) -> List[str]:
        return list(self._rows.keys())

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        row = self._rows.get(source_id)
        return RawListing(source_id=source_id, payload=row) if row else None

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        r = raw.payload
        title = (r.get("title") or "").strip()
        images = [u for u in (r.get("firstimage"), r.get("firstimage2")) if u]
        if not title or not images:
            return None

        addr = (r.get("addr1") or "").strip()
        bucket = r.get("_region", "기타")
        ctype = str(r.get("contenttypeid") or "12")
        type_label, category = CONTENT_TYPES.get(ctype, ("관광지", "자연/야외"))

        def _f(key: str) -> Optional[float]:
            try:
                v = float(r.get(key) or 0)
                return v or None
            except (TypeError, ValueError):
                return None

        # "20260226103000" → ISO, so a client can tell when the source last moved.
        mt = str(r.get("modifiedtime") or "")
        updated = (
            f"{mt[0:4]}-{mt[4:6]}-{mt[6:8]}T{mt[8:10]}:{mt[10:12]}:{mt[12:14]}"
            if len(mt) >= 14 else None
        )

        detail = " ".join(x for x in (addr, r.get("tel") or "") if x)

        return KoreanLocation(
            id=f"{self.id_prefix}{raw.source_id}",
            name=title,
            tagline=f"{type_label} · {addr}"[:90] if addr else type_label,
            region=f"{bucket} {addr.split()[1]}".strip() if len(addr.split()) > 1 else bucket,
            region_category=bucket,
            category=category,
            # Public sites are not rented by the hour; no price is invented.
            price_per_hour=0,
            price_per_day=0,
            min_hours=0,
            rating=0.0,
            review_count=0,
            images=images,
            specs=LocationSpec(
                area_sqm=0.0, area_pyeong=0.0, ceiling_height_m=0.0,
                window_direction="확인 필요", natural_light_type="확인 필요",
                golden_hour_window="확인 필요", power_capacity="확인 필요",
                parking_spots=0, has_freight_elevator=False,
                sound_recording_quality="확인 필요",
            ),
                tags=["한국관광공사", type_label] + (
                # Surfaced as a tag so the simulator and the UI can see it
                # without re-reading the source.
                ["변경금지"] if r.get("cpyrhtDivCd") == NO_DERIVATIVES else []
            ),
            permit_summary=(
                "공공 관광정보 기반 참고 로케이션입니다. 촬영 허가와 대관 가능 여부는 "
                "관리 주체에 직접 확인해야 합니다."
            ),
            citations=[
                ParallelCitation(
                    title=f"{title} — 한국관광공사 관광정보",
                    url=DATASET_PAGE,
                    excerpt=detail[:200] or title,
                    source_type="공공데이터포털 한국관광공사 관광정보 서비스 (오픈API)",
                    retrieval_timestamp="",
                    confidence_score=0.9,
                    verification_status="PUBLIC_RECORD",
                )
            ],
            listing_kind=Kind.REFERENCE,
            no_derivatives=(r.get("cpyrhtDivCd") == NO_DERIVATIVES),
            latitude=_f("mapy"),      # TourAPI: mapy is latitude, mapx longitude
            longitude=_f("mapx"),
            source_updated_at=updated,
        )

    def source_url(self, source_id: str) -> str:
        return DATASET_PAGE
