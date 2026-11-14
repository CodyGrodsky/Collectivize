"""Event discovery: a visibility-aware, filterable, explainable event list.

Which events can appear:
- Published events that haven't ended, plus cancelled events until their
  original end time (so people who planned to go, even late, find out).
- Only events the viewer may see, hosted by someone the viewer may see.
- Optional filters: text, tags, org (and the orgs under it), dates, and a
  radius around the viewer's current position.

Ranking: each event gets an estimated likelihood (0..1) that this viewer
would go, from the model in ranking.py. Its inputs are continuous signals:
friends going, whether your org hosts, how well the event matches your
tastes (preferences.py), distance relative to how far you usually go,
soonness, and the host's trust and recent activity. Results are ordered by
likelihood, with cancelled events always last. The biggest contributions are
turned into plain-language reasons, so ranking is never opaque.

The viewer's position is used for this search only and never stored.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from backend.objects import (
    ClaimState,
    DomainRuleError,
    Event,
    EventStatus,
    Freshness,
    GeoPoint,
    MemberOf,
    Organization,
    Person,
    RsvpStatus,
    Tag,
    Tagged,
)
from backend.objects.base import require_aware

from .connections import ConnectionService
from .events import EventService
from .explain import explain
from .hierarchy import HierarchyService
from .preferences import PreferenceService, TasteProfile
from .privacy import PrivacyService
from .ranking import (
    DEFAULT_WEIGHTS,
    MIN_TRAVEL_SCALE_KM,
    Contribution,
    EventSignals,
    RankingWeights,
    estimate,
    taste_match,
)
from .rsvps import RsvpService
from .store import GraphStore

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_TEXT_LENGTH = 200
DEFAULT_TRAVEL_KM = 2.0  # campus scale, until someone has travel history
HOST_TRUST = {ClaimState.CLAIMED: 1.0, ClaimState.CLAIM_PENDING: 0.4, ClaimState.UNCLAIMED: 0.0}
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True, kw_only=True)
class DiscoveryQuery:
    viewer_id: str | None = None  # None = logged-out visitor
    text: str = ""
    tag_ids: tuple[str, ...] = ()  # filter: event must match at least one
    preferred_tag_ids: tuple[str, ...] = ()  # extra interests for this search
    org_id: str | None = None
    include_related: bool = True  # with org_id: also descendant orgs
    starts_after: datetime | None = None
    starts_before: datetime | None = None
    include_cancelled: bool = True
    origin: GeoPoint | None = None  # where the viewer is now; never stored
    max_distance_km: float | None = None  # radius filter; needs origin
    include_unlocated: bool = True  # with a radius: keep events with no location
    event_ids: tuple[str, ...] = ()  # only these events (e.g. an agent's picks)
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_LIMIT:
            raise DomainRuleError(f"limit must be between 1 and {MAX_LIMIT}")
        if self.offset < 0:
            raise DomainRuleError("offset cannot be negative")
        if len(self.text) > MAX_TEXT_LENGTH:
            raise DomainRuleError(f"text is limited to {MAX_TEXT_LENGTH} characters")
        require_aware(self.starts_after, "starts_after")
        require_aware(self.starts_before, "starts_before")
        window = (self.starts_after, self.starts_before)
        if all(window) and self.starts_after >= self.starts_before:  # type: ignore[operator]
            raise DomainRuleError("starts_after must be before starts_before")
        if self.max_distance_km is not None:
            if self.origin is None:
                raise DomainRuleError("a distance limit needs your current position")
            radius = self.max_distance_km
            if isinstance(radius, bool) or not isinstance(radius, (int, float)):
                raise DomainRuleError("max_distance_km must be a number")
            if not math.isfinite(radius) or radius <= 0:
                raise DomainRuleError("max_distance_km must be a positive number")


@dataclass(frozen=True)
class RequestMatch:
    """How well one event fits a request made in words (from AI search)."""

    score: float  # 0..1
    reason: str = ""

    def __post_init__(self) -> None:
        if not (isinstance(self.score, (int, float)) and 0.0 <= self.score <= 1.0):
            raise DomainRuleError("a request match score must be between 0 and 1")


@dataclass(frozen=True)
class DiscoveryItem:
    event: Event
    host: Organization | Person  # primary host
    score: float  # estimated likelihood (0..1) that this viewer would go
    reasons: tuple[str, ...]
    host_freshness: Freshness | None  # None when a person hosts
    is_cancelled: bool
    friends_going: tuple[Person, ...] = ()  # connections going, if they allow it
    my_rsvp: RsvpStatus | None = None
    going_count: int | None = None  # None when hidden (too small) or none
    distance_km: float | None = None  # from the query's origin, if both known
    factors: tuple[Contribution, ...] = ()  # each signal's effect, for "why?"


@dataclass(frozen=True)
class DiscoveryPage:
    items: tuple[DiscoveryItem, ...]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True)
class _SearchContext:
    """Everything one search needs that is the same for every event."""

    query: DiscoveryQuery
    now: datetime
    org_scope: set[str] | None
    tag_labels: dict[str, str]
    friend_ids: set[str]
    my_org_names: dict[str, str]  # org id -> name, for the viewer's orgs
    profile: TasteProfile
    taste: dict[str, float]  # tag id -> weight
    travel_scale_km: float
    request_matches: Mapping[str, RequestMatch] = field(default_factory=dict)


class DiscoveryService:
    def __init__(
        self,
        store: GraphStore,
        events: EventService,
        hierarchy: HierarchyService,
        privacy: PrivacyService,
        connections: ConnectionService,
        rsvps: RsvpService,
        preferences: PreferenceService,
        weights: RankingWeights = DEFAULT_WEIGHTS,
    ) -> None:
        self._store = store
        self._events = events
        self._hierarchy = hierarchy
        self._privacy = privacy
        self._connections = connections
        self._rsvps = rsvps
        self._preferences = preferences
        self.weights = weights

    def search(
        self,
        query: DiscoveryQuery,
        now: datetime,
        *,
        request_matches: Mapping[str, RequestMatch] | None = None,
    ) -> DiscoveryPage:
        """`request_matches` (event id -> match) comes from AI search; events
        without an entry are neutral on that signal."""
        ctx = self._context(query, now, request_matches or {})
        items = [
            item
            for event in self._store.nodes(Event)
            if (item := self._consider(event, ctx))
        ]
        items.sort(key=lambda i: (i.is_cancelled, -i.score, i.event.starts_at, i.event.id))
        window = items[query.offset : query.offset + query.limit]
        return DiscoveryPage(
            items=tuple(window), total=len(items), offset=query.offset, limit=query.limit
        )

    def _context(
        self,
        query: DiscoveryQuery,
        now: datetime,
        request_matches: Mapping[str, RequestMatch],
    ) -> _SearchContext:
        viewer = query.viewer_id
        profile = self._preferences.profile(viewer, now)
        if profile.typical_trip_km is not None:
            scale = profile.typical_trip_km
        elif query.max_distance_km is not None:
            scale = query.max_distance_km / 2
        else:
            scale = DEFAULT_TRAVEL_KM
        return _SearchContext(
            query=query,
            now=now,
            org_scope=self._org_scope(query),
            tag_labels={t.id: t.label for t in self._store.nodes(Tag)},
            friend_ids=set(self._connections.connections_of(viewer)) if viewer else set(),
            my_org_names={org.id: org.name for org in self._my_orgs(viewer)},
            profile=profile,
            taste=profile.vector(frozenset(query.preferred_tag_ids)),
            travel_scale_km=max(scale, MIN_TRAVEL_SCALE_KM),
            request_matches=request_matches,
        )

    # --- filtering -----------------------------------------------------------

    def _consider(self, event: Event, ctx: _SearchContext) -> DiscoveryItem | None:
        query, now, org_scope = ctx.query, ctx.now, ctx.org_scope
        if query.event_ids and event.id not in query.event_ids:
            return None
        if not (event.is_active and event.is_discoverable(now)):
            return None
        is_cancelled = event.status is EventStatus.CANCELLED
        if is_cancelled and not query.include_cancelled:
            return None
        if not _in_window(event, query) or not _matches_text(event, query.text):
            return None
        distance = _distance(event, query.origin)
        if not _in_radius(distance, query):
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
        return self._rank(event, host, host_ids, tags, distance, ctx, is_cancelled)

    def _my_orgs(self, viewer_id: str | None) -> list[Organization]:
        if viewer_id is None:
            return []
        return [
            self._store.node(edge.target_id, Organization)
            for edge in self._store.edges(MemberOf, source_id=viewer_id)
            if edge.is_confirmed_current
        ]

    def _org_scope(self, query: DiscoveryQuery) -> set[str] | None:
        if query.org_id is None:
            return None
        scope = {query.org_id}
        if query.include_related:
            scope |= self._hierarchy.descendant_ids(query.org_id)
        return scope

    def _primary_host(self, event: Event) -> Organization | Person | None:
        try:
            return self._events.host_node(self._events.primary_host_id(event.id))
        except (DomainRuleError, LookupError):
            return None  # malformed hosting data never surfaces in discovery

    def _visible(
        self, viewer_id: str | None, event: Event, host: Organization | Person
    ) -> bool:
        """An event is never more visible than whoever hosts it."""
        return self._privacy.can_view(viewer_id, event) and self._privacy.can_view(
            viewer_id, host
        )  # same rule as PrivacyService.can_view_listing, with the host already loaded

    def event_tag_ids(self, event_id: str) -> set[str]:
        """An event's own tags plus those inherited from its host orgs."""
        return self._tag_ids(event_id, self._events.host_org_ids(event_id))

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
        host: Organization | Person,
        host_org_ids: set[str],
        tags: set[str],
        distance: float | None,
        ctx: _SearchContext,
        is_cancelled: bool,
    ) -> DiscoveryItem:
        viewer = ctx.query.viewer_id
        friends = self._rsvps.friends_going(viewer, event.id, friend_ids=ctx.friend_ids)
        my_hosts = sorted(ctx.my_org_names[i] for i in host_org_ids & ctx.my_org_names.keys())
        request = ctx.request_matches.get(event.id)
        signals = EventSignals(
            friends_going=len(friends),
            my_org_hosts=bool(my_hosts),
            affinity=taste_match(ctx.taste, tags),
            distance_km=distance,
            travel_scale_km=ctx.travel_scale_km,
            days_until=(event.starts_at - ctx.now).total_seconds() / SECONDS_PER_DAY,
            host_trust=HOST_TRUST[host.claim_state] if isinstance(host, Organization) else 0.0,
            host_inactive_days=(
                host.inactive_days(ctx.now) if isinstance(host, Organization) else None
            ),
            verification=event.provenance.verification,
            is_cancelled=is_cancelled,
            request_match=request.score if request else None,
        )
        result = estimate(signals, self.weights)
        mine = self._rsvps.mine(event.id, viewer) if viewer else None
        return DiscoveryItem(
            event=event,
            host=host,
            score=result.likelihood,
            reasons=explain(
                signals, result, host, my_hosts, tags, ctx.profile,
                frozenset(ctx.query.preferred_tag_ids), ctx.tag_labels,
                request_reason=request.reason if request else "",
            ),
            host_freshness=host.freshness(ctx.now) if isinstance(host, Organization) else None,
            is_cancelled=is_cancelled,
            friends_going=friends,
            my_rsvp=mine.status if mine else None,
            going_count=self._rsvps.going_count(event.id),
            distance_km=distance,
            factors=result.contributions,
        )


def _distance(event: Event, origin: GeoPoint | None) -> float | None:
    if origin is None or event.coordinates is None:
        return None
    return origin.distance_km(event.coordinates)


def _in_radius(distance: float | None, query: DiscoveryQuery) -> bool:
    if query.max_distance_km is None:
        return True
    if distance is None:
        return query.include_unlocated
    return distance <= query.max_distance_km


def _in_window(event: Event, query: DiscoveryQuery) -> bool:
    if query.starts_after is not None and event.starts_at < query.starts_after:
        return False
    return query.starts_before is None or event.starts_at < query.starts_before


def _matches_text(event: Event, text: str) -> bool:
    needle = normalize_text(text).strip()
    if not needle:
        return True
    return needle in normalize_text(f"{event.title} {event.description} {event.location}")


def normalize_text(text: str) -> str:
    """Comparable form: Unicode-normalized (so "café" typed either way
    matches) and case-folded."""
    return unicodedata.normalize("NFKC", text).casefold()
