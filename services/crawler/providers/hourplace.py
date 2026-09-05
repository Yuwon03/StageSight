"""
hourplace.co.kr — the source this catalogue started with.

A thin adapter over app/agent/tools/hourplace_ingest.py, which already does the
work: one sitemap request for the full id set, 14 image sitemaps for the photo
galleries, then one page fetch per listing for __NEXT_DATA__.

Rights are `ROBOTS_ALLOWED`, not `PARTNER_APPROVED`, and the distinction is
deliberate. hourplace.co.kr's robots.txt permits `User-Agent: Claude-User`, but
permission to crawl is not permission to republish photos and descriptions.
Before this is deployed publicly that basis has to be upgraded in writing — see
docs/venue-source-expansion-research.md.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.agent.tools.hourplace_ingest import (  # noqa: E402
    build_location,
    fetch_image_index,
    fetch_place_ids,
    fetch_place_meta,
)
from app.models.korean_locations import KoreanLocation  # noqa: E402

from .base import Kind, RawListing, Rights  # noqa: E402


class HourplaceProvider:
    name = "hourplace"
    id_prefix = "hp_"
    rights = Rights.ROBOTS_ALLOWED
    default_kind = Kind.BOOKABLE
    label = "아워플레이스"
    site_url = "https://hourplace.co.kr"

    def __init__(self) -> None:
        # The image sitemaps are 14 requests for ~12.7k galleries; fetched once
        # per pass and reused for every listing in it.
        self._gallery: dict = {}

    async def prepare(self, client: Any) -> None:
        self._gallery = await fetch_image_index(client)

    async def discover_ids(self, client: Any) -> List[str]:
        return [str(i) for i in await fetch_place_ids(client)]

    def should_fetch(self, source_id: str) -> bool:
        # A listing with no photo is dropped at normalize() under the data
        # policy, so fetching its detail page is a wasted request against a
        # source we are a guest on. ~1.3k of hourplace's ids have no gallery.
        return int(source_id) in self._gallery

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        meta = await fetch_place_meta(client, int(source_id))
        if not meta:
            return None
        return RawListing(source_id=source_id, payload={"meta": meta})

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        photos = self._gallery.get(int(raw.source_id), [])
        return build_location(int(raw.source_id), raw.payload["meta"], photos)

    def source_url(self, source_id: str) -> str:
        return f"{self.site_url}/place/{source_id}"
