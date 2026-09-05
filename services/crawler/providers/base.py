"""
The contract every venue source implements.

Adding a source must not mean editing the crawler. A provider declares what it
is allowed to do with the content it returns, how to enumerate its listings, and
how to turn one of them into a `KoreanLocation`; the worker does the rest —
scheduling, delisting, revisions — identically for all of them.

Two rules are enforced here rather than left to each adapter:

1. **Rights are declared, not assumed.** Being able to fetch a page is not
   permission to republish it. A provider states its basis and the registry
   refuses to enable one whose basis is `PENDING_PERMISSION`, so an adapter can
   be written and reviewed long before it is legally cleared to run.
2. **Availability is declared, not implied.** A public-record filming location
   is a real place, but it is not a listing anyone can book. `listing_kind`
   carries that distinction all the way to the UI.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from app.models.korean_locations import KoreanLocation  # noqa: E402


class Rights:
    """Why we believe we may display a source's content."""

    PUBLIC_OPEN_DATA = "public_open_data"      # open licence, redistribution allowed
    PARTNER_APPROVED = "partner_approved"      # written permission on file
    ROBOTS_ALLOWED = "robots_allowed"          # crawlable; republication unconfirmed
    PENDING_PERMISSION = "pending_permission"  # written to, not yet cleared — cannot run


class Kind:
    BOOKABLE = "bookable"
    INQUIRY_ONLY = "inquiry_only"
    REFERENCE = "reference"


@dataclass
class RawListing:
    """Whatever the source returned, before normalisation."""

    source_id: str
    payload: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ListingProvider(Protocol):
    name: str
    id_prefix: str
    rights: str
    default_kind: str

    async def discover_ids(self, client: Any) -> List[str]:
        """Every listing id this source currently offers."""

    async def fetch_listing(self, client: Any, source_id: str) -> Optional[RawListing]:
        """One listing's detail, or None if it is gone."""

    def should_fetch(self, source_id: str) -> bool:
        """Whether a detail fetch for this id can produce a usable row.

        Separate from `discover_ids`, which must keep returning the source's
        FULL id set: delisting is computed from that, so narrowing it here would
        delist every listing the filter skipped.
        """

    def normalize(self, raw: RawListing) -> Optional[KoreanLocation]:
        """Into the catalogue's shape, or None if the row is unusable.

        Returning None is the correct answer for a listing with no photo or no
        readable location — the catalogue would rather be smaller than carry a
        row it has to invent fields for.
        """


def stamp(loc: KoreanLocation, provider: ListingProvider, source_id: str, source_url: str) -> KoreanLocation:
    """Attach provenance. Called by the worker so no adapter can forget it."""
    from datetime import datetime, timezone

    loc.provider = provider.name
    loc.provider_listing_id = str(source_id)
    loc.source_url = source_url
    loc.rights_status = provider.rights
    if loc.listing_kind == "bookable" and provider.default_kind != Kind.BOOKABLE:
        loc.listing_kind = provider.default_kind
    loc.last_verified_at = datetime.now(timezone.utc).isoformat()
    return loc
