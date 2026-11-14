"""Organization node: any group with its own identity."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from urllib.parse import urlparse

from .base import DomainRuleError, GraphRecord, require_aware

QUIET_AFTER = timedelta(days=30)
STALE_AFTER = timedelta(days=90)


class OrgKind(Enum):
    CHAPTER = "chapter"
    NATIONAL = "national"
    COUNCIL = "council"
    CLUB = "club"
    INSTITUTION = "institution"


class ClaimState(Enum):
    UNCLAIMED = "unclaimed"  # created by Collectivize/import; staff-editable only
    CLAIM_PENDING = "claim_pending"  # admin access requested, verifying
    CLAIMED = "claimed"  # at least one verified admin


class Freshness(Enum):
    ACTIVE = "active"
    QUIET = "quiet"
    STALE = "stale"


def _validate_url(value: str | None, name: str) -> None:
    if value is None:
        return
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DomainRuleError(f"{name} must be an http(s) URL")


@dataclass(frozen=True, kw_only=True)
class Organization(GraphRecord):
    name: str
    kind: OrgKind
    claim_state: ClaimState = ClaimState.UNCLAIMED
    website_url: str | None = None  # the org's own site is the source of truth
    calendar_feed_url: str | None = None
    last_event_published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainRuleError("name is required")
        _validate_url(self.website_url, "website_url")
        _validate_url(self.calendar_feed_url, "calendar_feed_url")
        require_aware(self.last_event_published_at, "last_event_published_at")

    @property
    def is_claimed(self) -> bool:
        return self.claim_state is ClaimState.CLAIMED

    def inactive_days(self, now: datetime) -> float:
        """Days since the latest of profile confirmation and last event
        (0 if that is in the future)."""
        candidates = [self.provenance.last_known_good]
        if self.last_event_published_at is not None:
            candidates.append(self.last_event_published_at)
        return max((now - max(candidates)).total_seconds() / 86400, 0.0)

    def freshness(self, now: datetime) -> Freshness:
        """Derived from the latest of profile confirmation and last event."""
        candidates = [self.provenance.last_known_good]
        if self.last_event_published_at is not None:
            candidates.append(self.last_event_published_at)
        age = now - max(candidates)
        if age > STALE_AFTER:
            return Freshness.STALE
        if age > QUIET_AFTER:
            return Freshness.QUIET
        return Freshness.ACTIVE

    def with_claim_state(self, claim_state: ClaimState) -> Organization:
        return replace(self, claim_state=claim_state)

    def with_event_published(self, at: datetime) -> Organization:
        return replace(self, last_event_published_at=at)
