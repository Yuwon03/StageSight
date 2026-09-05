"""
Which sources exist, which may run, and why.

The gate is the point. An adapter can be written, reviewed and unit-tested long
before anyone is allowed to publish its content, so `PENDING_PERMISSION` sources
are listed here with the contact that has to clear them and are refused at
`enabled_providers()`. Nothing but editing this table turns one on — there is no
flag, and no "just for the demo" path, because the failure mode is republishing
a commercial platform's photographs without a licence.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import Kind, Rights
from .heritage import HeritageProvider
from .hourplace import HourplaceProvider
from .placehub import PlacehubProvider
from .public_data import PublicDataProvider
from .tourapi import TourApiProvider


class PendingProvider:
    """A source that is understood but not yet cleared to run.

    Kept as a real registry entry rather than a comment so the roadmap is
    visible in `/api/providers` and in the crawler's own log, and so turning one
    on is a one-line change once permission arrives.
    """

    def __init__(self, name: str, label: str, site_url: str, kind: str, contact: str, note: str):
        self.name = name
        self.label = label
        self.site_url = site_url
        self.id_prefix = f"{name[:2]}_"
        self.rights = Rights.PENDING_PERMISSION
        self.default_kind = kind
        self.contact = contact
        self.note = note


# Live sources, in the order the worker runs them.
PROVIDERS: Dict[str, object] = {
    "hourplace": HourplaceProvider(),
    "placehub": PlacehubProvider(),
    "public_data": PublicDataProvider(),
    "tourapi": TourApiProvider(),
    "heritage": HeritageProvider(),
}

# Researched, adapter-shaped, awaiting written permission.
# See docs/venue-source-expansion-research.md for the full assessment.
PENDING: Dict[str, PendingProvider] = {
    "unhide": PendingProvider(
        "unhide", "언하이드", "https://unhide.co.kr", Kind.BOOKABLE,
        "unhideofficial@gmail.com",
        "촬영 특화 큐레이션. 가격·면적·주차·시설이 정리돼 있어 파싱은 쉬우나 재게시 라이선스 미확인. 제휴/피드 문의 우선.",
    ),
    "filmkorea": PendingProvider(
        "filmkorea", "한국영상위원회", "https://www.koreafilm.or.kr", Kind.INQUIRY_ONLY,
        "영상위원회 로케이션팀",
        "공공시설·도로·폐시설·대형 스튜디오 등 아워플레이스에 없는 장소. 약관상 동의 없는 영리 이용 제한.",
    ),
    "spacecloud": PendingProvider(
        "spacecloud", "스페이스클라우드", "https://www.spacecloud.kr", Kind.BOOKABLE,
        "제휴 문의",
        "실매물·스튜디오 다수. 콘텐츠 표시 정책이 동의 없는 복제를 금지 — API/제휴 없이는 수집 금지.",
    ),
    "filmplace": PendingProvider(
        "filmplace", "필름플레이스", "https://www.filmplace.net", Kind.BOOKABLE,
        "제휴 문의",
        "이용약관이 bot·crawler·scraper 수집을 명시적으로 금지. 공식 API 외 경로 없음.",
    ),
}


def get_provider(name: str) -> Optional[object]:
    return PROVIDERS.get(name)


def enabled_providers(only: Optional[List[str]] = None) -> List[object]:
    """Providers the crawler may actually run.

    A source whose rights basis is still PENDING_PERMISSION is refused even if
    it is named explicitly on the command line.
    """
    out = []
    for name, p in PROVIDERS.items():
        if only and name not in only:
            continue
        if getattr(p, "rights", None) == Rights.PENDING_PERMISSION:
            continue
        out.append(p)
    return out


def roadmap() -> List[dict]:
    """Every source, live or not — what it is and what is blocking it."""
    rows = [
        {
            "provider": p.name,
            "label": getattr(p, "label", p.name),
            "site_url": getattr(p, "site_url", ""),
            "rights_status": p.rights,
            "listing_kind": p.default_kind,
            "enabled": True,
            "blocked_on": None,
        }
        for p in PROVIDERS.values()
    ]
    rows += [
        {
            "provider": p.name,
            "label": p.label,
            "site_url": p.site_url,
            "rights_status": p.rights,
            "listing_kind": p.default_kind,
            "enabled": False,
            "blocked_on": p.contact,
            "note": p.note,
        }
        for p in PENDING.values()
    ]
    return rows
