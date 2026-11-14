"""What a person interacts with most: their top orgs and recent events.

Used by AI search (to understand the person) and by data exports. Only the
person's own facts are read, and anything they marked `recommendation_use =
NONE` is skipped. Recent activity counts more (it fades with
INTERACTION_HALF_LIFE).

How an org's interaction score is built:
- a current, confirmed membership: MEMBERSHIP_POINTS (+ADMIN_POINTS for admins)
- following it: FOLLOW_POINTS
- each event of theirs you RSVP'd going / interested: GOING / INTERESTED points,
  faded by how long ago you said so
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.objects import (
    Event,
    Follows,
    Hosts,
    MemberOf,
    Organization,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Tagged,
)

from .preferences import decay
from .store import GraphStore

INTERACTION_HALF_LIFE = timedelta(days=90)
MEMBERSHIP_POINTS = 3.0
ADMIN_POINTS = 1.0
FOLLOW_POINTS = 1.0
GOING_POINTS = 1.0
INTERESTED_POINTS = 0.5
DEFAULT_TOP_ORGS = 5
DEFAULT_RECENT_EVENTS = 8


@dataclass(frozen=True)
class OrgInteraction:
    org: Organization
    score: float
    is_member: bool


@dataclass(frozen=True)
class EventInteraction:
    event: Event
    status: RsvpStatus
    at: datetime
    tag_ids: frozenset[str]


class ActivityService:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    def top_orgs(
        self, person_id: str, now: datetime, limit: int = DEFAULT_TOP_ORGS
    ) -> list[OrgInteraction]:
        scores: dict[str, float] = {}
        members: set[str] = set()
        for edge in self._store.edges(MemberOf, source_id=person_id):
            if edge.is_confirmed_current and _consented(edge):
                points = MEMBERSHIP_POINTS + (ADMIN_POINTS if edge.is_admin else 0.0)
                scores[edge.target_id] = scores.get(edge.target_id, 0.0) + points
                members.add(edge.target_id)
        for edge in self._store.edges(Follows, source_id=person_id):
            if _consented(edge):
                scores[edge.target_id] = scores.get(edge.target_id, 0.0) + FOLLOW_POINTS
        for rsvp in self._rsvps(person_id):
            weight = _rsvp_points(rsvp) * decay(now - rsvp.updated_at, INTERACTION_HALF_LIFE)
            for org_id in self._host_orgs(rsvp.target_id):
                scores[org_id] = scores.get(org_id, 0.0) + weight
        ranked = sorted(
            (
                OrgInteraction(org=org, score=score, is_member=org.id in members)
                for org_id, score in scores.items()
                if score > 0 and (org := self._org(org_id)) is not None
            ),
            key=lambda o: (-o.score, o.org.name, o.org.id),
        )
        return ranked[: max(limit, 0)]

    def recent_events(
        self, person_id: str, limit: int = DEFAULT_RECENT_EVENTS
    ) -> list[EventInteraction]:
        found = []
        for rsvp in self._rsvps(person_id):
            if not self._store.has_node(rsvp.target_id):
                continue
            event = self._store.node(rsvp.target_id, Event)
            found.append(
                EventInteraction(
                    event=event,
                    status=rsvp.status,
                    at=rsvp.updated_at,
                    tag_ids=frozenset(self._tags(event.id)),
                )
            )
        found.sort(key=lambda e: (e.at, e.event.id), reverse=True)
        return found[: max(limit, 0)]

    # --- internals -------------------------------------------------------------

    def _rsvps(self, person_id: str) -> list[Rsvp]:
        return [
            r for r in self._store.edges(Rsvp, source_id=person_id)
            if _consented(r) and r.status is not RsvpStatus.NOT_GOING
        ]

    def _host_orgs(self, event_id: str) -> set[str]:
        return {
            h.source_id
            for h in self._store.edges(Hosts, target_id=event_id)
            if h.is_effective and isinstance(self._store.any_node(h.source_id), Organization)
        }

    def _tags(self, event_id: str) -> set[str]:
        subjects = {event_id} | self._host_orgs(event_id)
        return {t.target_id for s in subjects for t in self._store.edges(Tagged, source_id=s)}

    def _org(self, org_id: str) -> Organization | None:
        if not self._store.has_node(org_id):
            return None
        node = self._store.any_node(org_id)
        return node if isinstance(node, Organization) and node.is_active else None


def _consented(edge: MemberOf | Follows | Rsvp) -> bool:
    return edge.recommendation_use is not RecommendationUse.NONE


def _rsvp_points(rsvp: Rsvp) -> float:
    return GOING_POINTS if rsvp.status is RsvpStatus.GOING else INTERESTED_POINTS
