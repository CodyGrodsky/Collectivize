"""Event discovery: a visibility-aware, filterable, explainable event list.

Which events can appear:
- Published events that haven't ended, plus cancelled events until their
  original end time (so people who planned to go, even late, find out).
- Only events the viewer may see, hosted by an org the viewer may see.

Ranking (higher first; ties broken by start time). Every factor that moves
an event adds a plain-language reason, so ranking is never opaque:
- Host trust: claimed (verified) > claim pending > unclaimed listing.
- Host freshness: quiet and stale hosts are pushed down.
- Event provenance: verified details up, disputed details down.
- Interests: each matching tag the viewer chose ("preferred") boosts.
- Soonness: sooner events rank a little higher.
- Cancelled events always sink to the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.objects import (
    ClaimState,
    DomainRuleError,
    Event,
    EventStatus,
    Freshness,
    Organization,
    Tag,
    Tagged,
    VerificationStatus,
)

from .events import EventService
from .hierarchy import HierarchyService
from .privacy import PrivacyService
from .store import GraphStore

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_TEXT_LENGTH = 200

SOON_HORIZON = timedelta(days=30)
STARTING_SOON = timedelta(hours=48)

HOST_TRUST_SCORE = {
    ClaimState.CLAIMED: 3.0,
    ClaimState.CLAIM_PENDING: 1.0,
    ClaimState.UNCLAIMED: 0.0,
}
HOST_TRUST_REASON = {
    ClaimState.CLAIMED: "Hosted by a verified org",
    ClaimState.CLAIM_PENDING: "Host's claim is being verified",
    ClaimState.UNCLAIMED: "Unclaimed listing: not managed by the org itself",
}
FRESHNESS_SCORE = {Freshness.ACTIVE: 0.0, Freshness.QUIET: -1.0, Freshness.STALE: -3.0}
FRESHNESS_REASON = {
    Freshness.QUIET: "Host hasn't posted recently",
    Freshness.STALE: "Host info may be out of date",
}
PROVENANCE_SCORE = {
    VerificationStatus.VERIFIED: 1.0,
    VerificationStatus.UNVERIFIED: 0.0,
    VerificationStatus.DISPUTED: -3.0,
}
PROVENANCE_REASON = {
    VerificationStatus.VERIFIED: "Event details verified",
    VerificationStatus.DISPUTED: "Event details are disputed",
}
INTEREST_MATCH_SCORE = 2.5
SOONNESS_MAX_SCORE = 2.0
CANCELLED_SCORE = -100.0
CANCELLED_REASON = "Cancelled: this event is not happening"


@dataclass(frozen=True, kw_only=True)
class DiscoveryQuery:
    viewer_id: str | None = None  # None = logged-out visitor
    text: str = ""
    tag_ids: tuple[str, ...] = ()  # filter: event must match at least one
    preferred_tag_ids: tuple[str, ...] = ()  # boost only, never filters
    org_id: str | None = None
    include_related: bool = True  # with org_id: also descendant orgs
    starts_after: datetime | None = None
    starts_before: datetime | None = None
    include_cancelled: bool = True
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_LIMIT:
            raise DomainRuleError(f"limit must be between 1 and {MAX_LIMIT}")
        if self.offset < 0:
            raise DomainRuleError("offset cannot be negative")
        if len(self.text) > MAX_TEXT_LENGTH:
            raise DomainRuleError(f"text is limited to {MAX_TEXT_LENGTH} characters")
        window = (self.starts_after, self.starts_before)
        if all(window) and self.starts_after >= self.starts_before:  # type: ignore[operator]
            raise DomainRuleError("starts_after must be before starts_before")


@dataclass(frozen=True)
class DiscoveryItem:
    event: Event
    host: Organization  # primary host
    score: float
    reasons: tuple[str, ...]
    host_freshness: Freshness
    is_cancelled: bool


@dataclass(frozen=True)
class DiscoveryPage:
    items: tuple[DiscoveryItem, ...]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class DiscoveryService:
    def __init__(
        self,
        store: GraphStore,
        events: EventService,
        hierarchy: HierarchyService,
        privacy: PrivacyService,
    ) -> None:
        self._store = store
        self._events = events
        self._hierarchy = hierarchy
        self._privacy = privacy

    def search(self, query: DiscoveryQuery, now: datetime) -> DiscoveryPage:
        org_scope = self._org_scope(query)
        tag_labels = {t.id: t.label for t in self._store.nodes(Tag)}
        items = [
            item
            for event in self._store.nodes(Event)
            if (item := self._consider(event, query, now, org_scope, tag_labels))
        ]
        items.sort(key=lambda i: (-i.score, i.event.starts_at, i.event.id))
        window = items[query.offset : query.offset + query.limit]
        return DiscoveryPage(
            items=tuple(window), total=len(items), offset=query.offset, limit=query.limit
        )

    # --- filtering -----------------------------------------------------------

    def _consider(
        self,
        event: Event,
        query: DiscoveryQuery,
        now: datetime,
        org_scope: set[str] | None,
        tag_labels: dict[str, str],
    ) -> DiscoveryItem | None:
        if not (event.is_active and event.is_discoverable(now)):
            return None
        is_cancelled = event.status is EventStatus.CANCELLED
        if is_cancelled and not query.include_cancelled:
            return None
        if not _in_window(event, query) or not _matches_text(event, query.text):
            return None
        host_ids = self._events.host_org_ids(event.id)
        if org_scope is not None and not host_ids & org_scope:
            return None
        host = self._primary_host(event)
        if host is None or not self._visible(query.viewer_id, event, host):
            return None
        tags = self._tag_ids(event.id, host_ids)
        if query.tag_ids and not tags & set(query.tag_ids):
            return None
        return self._rank(event, host, tags, query, now, tag_labels, is_cancelled)

    def _org_scope(self, query: DiscoveryQuery) -> set[str] | None:
        if query.org_id is None:
            return None
        scope = {query.org_id}
        if query.include_related:
            scope |= self._hierarchy.descendant_ids(query.org_id)
        return scope

    def _primary_host(self, event: Event) -> Organization | None:
        try:
            return self._store.node(self._events.primary_host_id(event.id), Organization)
        except (DomainRuleError, LookupError):
            return None  # malformed hosting data never surfaces in discovery

    def _visible(self, viewer_id: str | None, event: Event, host: Organization) -> bool:
        """An event is never more visible than the org hosting it."""
        return self._privacy.can_view(viewer_id, event) and self._privacy.can_view(
            viewer_id, host
        )

    def _tag_ids(self, event_id: str, host_ids: set[str]) -> set[str]:
        """The event's own tags plus tags inherited from its host orgs."""
        subjects = {event_id} | host_ids
        return {
            edge.target_id
            for subject in subjects
            for edge in self._store.edges(Tagged, source_id=subject)
        }

    # --- ranking -------------------------------------------------------------

    def _rank(
        self,
        event: Event,
        host: Organization,
        tags: set[str],
        query: DiscoveryQuery,
        now: datetime,
        tag_labels: dict[str, str],
        is_cancelled: bool,
    ) -> DiscoveryItem:
        freshness = host.freshness(now)
        verification = event.provenance.verification
        score = (
            HOST_TRUST_SCORE[host.claim_state]
            + FRESHNESS_SCORE[freshness]
            + PROVENANCE_SCORE[verification]
            + _soonness(event, now)
        )
        reasons = [HOST_TRUST_REASON[host.claim_state]]
        for lookup, key in ((FRESHNESS_REASON, freshness), (PROVENANCE_REASON, verification)):
            if key in lookup:
                reasons.append(lookup[key])
        for tag_id in sorted(tags & set(query.preferred_tag_ids)):
            score += INTEREST_MATCH_SCORE
            reasons.append(f"Matches your interest: {tag_labels.get(tag_id, tag_id)}")
        if is_cancelled:
            score += CANCELLED_SCORE
            reasons.insert(0, CANCELLED_REASON)
        elif timedelta(0) <= event.starts_at - now <= STARTING_SOON:
            reasons.append("Starts within 48 hours")
        return DiscoveryItem(
            event=event,
            host=host,
            score=score,
            reasons=tuple(reasons),
            host_freshness=freshness,
            is_cancelled=is_cancelled,
        )


def _in_window(event: Event, query: DiscoveryQuery) -> bool:
    if query.starts_after is not None and event.starts_at < query.starts_after:
        return False
    return query.starts_before is None or event.starts_at < query.starts_before


def _matches_text(event: Event, text: str) -> bool:
    needle = text.strip().casefold()
    if not needle:
        return True
    haystack = f"{event.title} {event.description} {event.location}".casefold()
    return needle in haystack


def _soonness(event: Event, now: datetime) -> float:
    """0..SOONNESS_MAX_SCORE, highest for events starting now."""
    until = max(event.starts_at - now, timedelta(0))
    remaining = max(0.0, 1 - until / SOON_HORIZON)
    return SOONNESS_MAX_SCORE * remaining
