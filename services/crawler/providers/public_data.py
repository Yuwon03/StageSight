"""
공공데이터포털 — 촬영지 공개 데이터.

The only sources on the roadmap whose licence already permits redistribution
(이용허락범위 제한 없음), which is why they are the first added after hourplace.
They are also the reason `listing_kind` exists: these are places productions
have actually shot at, recorded by a public body. They are not rental listings.
Nobody has checked whether they can be booked, at what price, or whether they
still stand. They enter the catalogue as `reference` and the UI says so.

Verified datasets (checked against data.go.kr, not taken from a summary):

  15052437  서울특별시_드라마CF촬영장소 정보          CSV
  15109020  한국영상자료원_영화 로케이션 촬영이력      CSV, 이용허락범위 제한 없음
  15148794  한국영상자료원_영화 로케이션별 촬영횟수    CSV

Two ways in:
  * `--public-data-csv <path>`  a file downloaded from data.go.kr in a browser
  * `PUBLIC_DATA_API_KEY`       a service key for the odcloud open API

The portal's own page says file data downloads without login, but its download
endpoint returns the portal's HTML shell to a plain HTTP client, so the file has
to come from a browser. Hence the CSV path being the primary one.

With neither, this provider imports nothing and says why. It never invents rows:
a catalogue that fabricates locations to look complete is precisely the failure
this repo was built to avoid.
"""
from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.models.korean_locations import KoreanLocation, LocationSpec  # noqa: E402
from app.models.schemas import ParallelCitation  # noqa: E402

from .base import Kind, RawListing, Rights  # noqa: E402

API_BASE = "https://api.odcloud.kr/api"
# Default to the Seoul drama/CF filming-location register; override with
# --public-data-dataset. The id is verified, not copied from a summary — an
# earlier revision of this file pointed at 15069776, which is a Ministry of
# Education scholarship dataset.
DATASET = "15052437"

# Column names vary between the CSV export and the API. Both spellings are
# accepted rather than guessing which one a given download used.
FIELDS = {
    "name": ("장소명", "시설명", "명칭", "PLACE_NM"),
    "address": ("소재지도로명주소", "주소", "소재지지번주소", "ADDR"),
    "desc": ("장소설명", "설명", "내용", "DESC"),
    "hours": ("운영시간", "이용시간"),
    "closed": ("휴무일", "쉬는날"),
    "lat": ("위도", "LAT", "latitude"),
    "lon": ("경도", "LON", "longitude"),
    "media": ("미디어명", "작품명", "촬영작품"),
}

REGION_PREFIXES = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]


def _pick(row: Dict[str, Any], keys) -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _region_of(address: str) -> tuple[str, str]:
    """(display region, region bucket). Unparseable addresses are labelled, not
    guessed into a default city."""
    for p in REGION_PREFIXES:
        if address.startswith(p) or address.startswith(p[:2]):
            parts = address.split()
            detail = parts[1] if len(parts) > 1 else ""
            return (f"{p} {detail}".strip(), p)
    return ("지역 확인 필요", "기타")


class PublicDataProvider:
    name = "public_data"
    id_prefix = "pd_"
    rights = Rights.PUBLIC_OPEN_DATA
    default_kind = Kind.REFERENCE
    label = "공공데이터포털"
    site_url = "https://www.data.go.kr/data/15052437/fileData.do"

    def __init__(
        self,
        csv_path: Optional[str] = None,
        api_key: Optional[str] = None,
        dataset: Optional[str] = None,
    ) -> None:
        self.csv_path = csv_path
        self.api_key = api_key or os.getenv("PUBLIC_DATA_API_KEY", "")
        self.dataset = dataset or os.getenv("PUBLIC_DATA_DATASET", DATASET)
        self._rows: Dict[str, Dict[str, Any]] = {}
        self.unavailable_reason: Optional[str] = None

    # ── loading ────────────────────────────────────────────────────────────
    async def prepare(self, client: Any) -> None:
        if self.csv_path:
            self._load_csv(Path(self.csv_path))
        elif self.api_key:
            await self._load_api(client)
        else:
            self.unavailable_reason = (
                "공공데이터 소스가 설정되지 않았습니다. 브라우저에서 "
                "data.go.kr/data/15052437/fileData.do 의 CSV를 받아 --public-data-csv 로 "
                "지정하거나, 활용신청 후 PUBLIC_DATA_API_KEY 를 설정하세요."
            )

    def _load_csv(self, path: Path) -> None:
        if not path.exists():
            self.unavailable_reason = f"CSV를 찾을 수 없습니다: {path}"
            return
        # Portal exports are usually CP949; UTF-8 downloads exist too.
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            self.unavailable_reason = f"인코딩을 판별할 수 없습니다: {path}"
            return
        for i, row in enumerate(csv.DictReader(io.StringIO(text))):
            name = _pick(row, FIELDS["name"])
            if name:
                self._rows[f"csv{i}"] = row

    async def _load_api(self, client: Any) -> None:
        page = 1
        while True:
            r = await client.get(
                f"{API_BASE}/{self.dataset}/v1/uddi",
                params={"page": page, "perPage": 1000, "serviceKey": self.api_key},
                timeout=30.0,
            )
            if r.status_code != 200:
                self.unavailable_reason = f"공공데이터 API {r.status_code} — 서비스 키를 확인하세요."
                return
            body = r.json()
            data = body.get("data") or []
            for i, row in enumerate(data):
                name = _pick(row, FIELDS["name"])
                if name:
                    self._rows[f"api{page}_{i}"] = row
            if len(data) < 1000:
                return
            page += 1

    # ── provider contract ──────────────────────────────────────────────────
    async def discover_ids(self, client: Any) -> List[str]:
        return list(self._rows.keys())

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        row = self._rows.get(source_id)
        return RawListing(source_id=source_id, payload=row) if row else None

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        row = raw.payload
        name = _pick(row, FIELDS["name"])
        address = _pick(row, FIELDS["address"])
        if not name:
            return None

        region, bucket = _region_of(address)
        desc = _pick(row, FIELDS["desc"])
        media = _pick(row, FIELDS["media"])

        def _f(keys) -> Optional[float]:
            v = _pick(row, keys)
            try:
                return float(v) if v else None
            except ValueError:
                return None

        tags = ["공공데이터", "과거 촬영지"]
        if media:
            tags.append(media[:20])

        return KoreanLocation(
            id=f"{self.id_prefix}{raw.source_id}",
            name=name,
            # Says what this row is, in the one place a user cannot miss.
            tagline=(desc[:90] or f"{media} 촬영지" if media else "미디어 촬영 기록이 있는 장소"),
            region=region,
            region_category=bucket,
            category="자연/야외",
            # No price is known and none is invented; the UI shows "가격 문의".
            price_per_hour=0,
            price_per_day=0,
            min_hours=0,
            rating=0.0,
            review_count=0,
            # The dataset carries no photographs. A reference row with no image
            # is honest; borrowing one from elsewhere would not be.
            images=[],
            specs=LocationSpec(
                area_sqm=0.0,
                area_pyeong=0.0,
                ceiling_height_m=0.0,
                window_direction="확인 필요",
                natural_light_type="확인 필요",
                golden_hour_window="확인 필요",
                power_capacity="확인 필요",
                parking_spots=0,
                has_freight_elevator=False,
                sound_recording_quality="확인 필요",
            ),
            tags=tags,
            permit_summary=(
                "공공데이터 기반 과거 촬영지 기록입니다. 현재 대관 가능 여부와 촬영 허가는 "
                "관리 주체에 직접 확인해야 합니다."
            ),
            citations=[
                ParallelCitation(
                    title=f"{name} — 한국문화정보원 미디어 촬영지",
                    url=self.site_url,
                    excerpt=(f"{address} · {desc}"[:200] if address or desc else name),
                    source_type="공공데이터포털 (data.go.kr) 개방 데이터",
                    retrieval_timestamp="",
                    confidence_score=0.9,
                    verification_status="PUBLIC_RECORD",
                )
            ],
            listing_kind=Kind.REFERENCE,
            latitude=_f(FIELDS["lat"]),
            longitude=_f(FIELDS["lon"]),
        )

    def source_url(self, source_id: str) -> str:
        return self.site_url
