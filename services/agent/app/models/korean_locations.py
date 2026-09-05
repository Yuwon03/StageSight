from typing import List, Optional
from pydantic import BaseModel, Field
from app.models.schemas import ParallelCitation

class LocationSpec(BaseModel):
    area_sqm: float
    area_pyeong: float
    ceiling_height_m: float
    window_direction: str
    natural_light_type: str
    golden_hour_window: str
    power_capacity: str
    parking_spots: int
    has_freight_elevator: bool
    sound_recording_quality: str  # e.g. "동시녹음 완벽 (방음 시공)", "보통 (주변 생활 소음 가능)"


class OriginalLocationText(BaseModel):
    """Source-language values retained on an English display copy.

    These are not shown in the English UI, but production tools can still use
    the exact Korean listing title/address when researching permits or matching
    the source. The catalogue row itself is never translated or overwritten.
    """
    name: str
    region: str
    category: str
    window_direction: str
    citation_excerpts: List[str] = Field(default_factory=list)

class KoreanLocation(BaseModel):
    """One venue, from whatever source supplied it.

    The provenance block below is what makes the catalogue multi-source. Every
    field defaults, so rows written before these existed still validate and are
    read back as hourplace bookables — the shape this catalogue had when it was
    single-source.
    """
    id: str
    name: str
    tagline: str
    region: str          # e.g. "서울 성수", "서울 종로", "경기 양평", "경기 파주"
    region_category: str # e.g. "서울", "경기", "제주", "부산"
    category: str        # "모던 스튜디오", "전통 한옥", "자연/야외", "빈티지/창고", "럭셔리 하우스", "카페/갤러리"
    price_per_hour: int  # in KRW (e.g. 80000)
    price_per_day: int   # in KRW (e.g. 800000)
    min_hours: int
    rating: float
    review_count: int
    images: List[str]
    specs: LocationSpec
    tags: List[str]
    permit_summary: str
    citations: List[ParallelCitation]

    # ── Provenance ─────────────────────────────────────────────────────────
    # Which source supplied this row, and its id over there. `id` stays the
    # catalogue-wide key (prefix + provider id) so links survive re-ingests.
    provider: str = "hourplace"
    provider_listing_id: str = ""
    source_url: str = ""

    # What a user can actually do with it. The distinction is the whole point of
    # taking in public-record locations: a 2022 filming-location register is a
    # real place worth scouting but is NOT a rentable listing, and showing the
    # two the same way would be the fabrication this repo exists to avoid.
    #   bookable     — listed for rent right now, with a price and a booking URL
    #   inquiry_only — real venue, rental possible, but arranged by contact
    #   reference    — a place films have used; availability unknown
    listing_kind: str = "bookable"

    # Set once the same physical venue is found on more than one platform, so
    # the UI can show one card with several booking links instead of duplicates.
    canonical_id: Optional[str] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # When the source itself last changed the listing, vs when we last confirmed
    # it is still there. Different questions; a crawl answers only the second.
    source_updated_at: Optional[str] = None
    last_verified_at: Optional[str] = None

    # How we are allowed to display this content. Being able to fetch a page is
    # not permission to republish it, so the basis is recorded per row rather
    # than assumed — see docs/venue-source-expansion-research.md.
    #   public_open_data   — open licence, redistribution allowed
    #   partner_approved   — written permission from the platform
    #   robots_allowed     — crawling permitted; republication rights unconfirmed
    rights_status: str = "robots_allowed"

    # Some sources licence a photograph for display but forbid altering it —
    # 한국관광공사 marks these cpyrhtDivCd=Type3 (제1유형 + 변경금지), and they are
    # 55% of its rows. The AI frame simulator exists to alter photographs, so it
    # must refuse these. Enforced at the API, not left to the UI.
    no_derivatives: bool = False

    # Read-side catalogue metadata added by SQLite. Keeping it on the model is
    # important because English localisation round-trips rows through this
    # schema before returning them to the UI.
    is_new: bool = False
    first_seen: Optional[str] = None

    # Present only on a localised API response. Stored catalogue records remain
    # Korean, which preserves source fidelity and keeps filtering deterministic.
    display_language: str = "ko"
    original_text: Optional[OriginalLocationText] = None
