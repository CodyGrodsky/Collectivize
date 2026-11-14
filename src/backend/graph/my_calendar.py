"""A person's own calendar: their events, and the community's.

For a time window (at most MAX_RANGE long), every event the viewer may see
that overlaps it, split in two:

- **mine**: events the viewer personally chose: hosts (themselves, as an
  org admin, or as a co-host), is going to, marked "maybe", or starred.
- **community**: every other listed event the viewer may see, capped at
  MAX_COMMUNITY (the cap is reported, never silent). Events run by one of
  the viewer's orgs carry the MY_ORG relation here, so they stand out.

Each entry lists every relation that applies.

Drafts appear only for the people who can edit them; archived events
never appear; cancelled events stay, flagged, so plans can be changed.
Un-RSVPing or un-starring an event takes it out of "mine" (it may still
appear under community, if it's listed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    MemberOf,
    Organization,
    Person,
    Rsvp,
    RsvpStatus,
)
from backend.objects.base import require_aware

from .events import EventService
from .privacy import PrivacyService
from .stars import StarService
from .store import GraphStore

MAX_RANGE = timedelta(days=62)  # a month view with its leading/trailing weeks
MAX_COMMUNITY = 400
PERSONAL = frozenset({"hosting", "going", "maybe", "starred"})


class Relation(Enum):
    HOSTING = "hosting"
    GOING = "going"
    MAYBE = "maybe"
    STARRED = "starred"
    MY_ORG = "my_org"


@dataclass(frozen=True)
class CalendarEntry:
    event: Event
    host: Organization | Person | None
    relations: frozenset[Relation]


@dataclass(frozen=True)
class PersonalCalendar:
    start: datetime
    end: datetime
    mine: tuple[CalendarEntry, ...]
    community: tuple[CalendarEntry, ...]
    community_truncated: bool


class PersonalCalendarService:
    def __init__(
        self,
        store: GraphStore,
        events: EventService,
        privacy: PrivacyService,
        stars: StarService,
    ) -> None:
        self._store = store
        self._events = events
        self._privacy = privacy
        self._stars = stars

    def for_viewer(
        self, viewer_id: str | None, start: datetime, end: datetime
    ) -> PersonalCalendar:
        """The viewer's events and the community's that overlap [start, end)."""
        _check_window(start, end)
        if viewer_id is not None:
            self._store.node(viewer_id, Person)
        rsvps = self._rsvps(viewer_id)
        starred = self._stars.starred_event_ids(viewer_id)
        my_orgs = self._my_orgs(viewer_id)
        mine: list[CalendarEntry] = []
        community: list[CalendarEntry] = []
        for event in self._store.nodes(Event):
            if not _overlaps(event, start, end) or not self._shown(event, viewer_id):
                continue
            relations = self._relations(event, viewer_id, rsvps, starred, my_orgs)
            entry = CalendarEntry(event=event, host=self._host(event), relations=relations)
            is_mine = any(r.value in PERSONAL for r in relations)
            (mine if is_mine else community).append(entry)
        order = lambda e: (e.event.starts_at, e.event.id)
        mine.sort(key=order)
        community.sort(key=order)
        return PersonalCalendar(
            start=start,
            end=end,
            mine=tuple(mine),
            community=tuple(community[:MAX_COMMUNITY]),
            community_truncated=len(community) > MAX_COMMUNITY,
        )

    # --- internals -------------------------------------------------------------

    def _shown(self, event: Event, viewer_id: str | None) -> bool:
        if not event.is_active or event.status is EventStatus.ARCHIVED:
            return False
        return self._privacy.can_view_listing(viewer_id, event)

    def _relations(
        self,
        event: Event,
        viewer_id: str | None,
        rsvps: dict[str, RsvpStatus],
        starred: set[str],
        my_orgs: set[str],
    ) -> frozenset[Relation]:
        if viewer_id is None:
            return frozenset()
        found = set()
        if self._events.can_edit(viewer_id, event.id):
            found.add(Relation.HOSTING)
        status = rsvps.get(event.id)
        if status is RsvpStatus.GOING:
            found.add(Relation.GOING)
        elif status is RsvpStatus.INTERESTED:
            found.add(Relation.MAYBE)
        if event.id in starred:
            found.add(Relation.STARRED)
        if my_orgs & self._events.host_org_ids(event.id):
            found.add(Relation.MY_ORG)
        return frozenset(found)

    def _rsvps(self, viewer_id: str | None) -> dict[str, RsvpStatus]:
        if viewer_id is None:
            return {}
        return {r.target_id: r.status for r in self._store.edges(Rsvp, source_id=viewer_id)}

    def _my_orgs(self, viewer_id: str | None) -> set[str]:
        if viewer_id is None:
            return set()
        return {
            m.target_id for m in self._store.edges(MemberOf, source_id=viewer_id)
            if m.is_confirmed_current
        }

    def _host(self, event: Event) -> Organization | Person | None:
        try:
            return self._events.host_node(self._events.primary_host_id(event.id))
        except (DomainRuleError, LookupError):
            return None  # malformed hosting data: still show the event


def _check_window(start: datetime, end: datetime) -> None:
    require_aware(start, "start")
    require_aware(end, "end")
    if start is None or end is None or start >= end:
        raise DomainRuleError("the calendar window must start before it ends")
    if end - start > MAX_RANGE:
        raise DomainRuleError(f"the calendar window is limited to {MAX_RANGE.days} days")


def _overlaps(event: Event, start: datetime, end: datetime) -> bool:
    return event.starts_at < end and event.ends_at > start
