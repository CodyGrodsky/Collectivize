"""RSVP rules (user_Orgs.md, Person -> Event: RSVP).

- One RSVP per person per event: going, interested or not going. It can
  be changed or cleared, but only while the event is published and
  hasn't ended. When the event ends, RSVPs freeze.
- You can only RSVP to an event you can see. Anything else looks like a
  missing event.
- A full event (capacity reached) takes no new "going" RSVPs.
- Who can see your RSVPs is your own setting (`Person.rsvp_visibility`:
  logged-in users, your connections, or nobody). Each RSVP carries it, so
  the normal privacy rules decide who sees each one. Changing the setting
  updates all your RSVPs.
- Named attendees appear only when their setting allows it. Totals count
  everyone but are hidden below MIN_PUBLIC_COUNT, so a small number can't
  identify anyone.
- If the person shares where they are (`origin`) when they say "going",
  the distance to the event feeds their private travel average (see
  preferences.py). The position is used once and not stored.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    GeoPoint,
    Person,
    Provenance,
    ProvenanceMethod,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Visibility,
)

from .connections import ConnectionService
from .errors import NotFoundError, PermissionDeniedError
from .preferences import PreferenceService
from .privacy import PrivacyService
from .store import GraphStore

MIN_PUBLIC_COUNT = 5
RSVP_AUDIENCES = frozenset({Visibility.NETWORK, Visibility.CONNECTIONS, Visibility.PRIVATE})


class RsvpService:
    def __init__(
        self,
        store: GraphStore,
        privacy: PrivacyService,
        connections: ConnectionService,
        preferences: PreferenceService,
    ) -> None:
        self._store = store
        self._privacy = privacy
        self._connections = connections
        self._preferences = preferences

    # --- your own RSVP -------------------------------------------------------

    def respond(
        self,
        event_id: str,
        person_id: str,
        status: RsvpStatus | None,
        now: datetime,
        *,
        origin: GeoPoint | None = None,
    ) -> Rsvp | None:
        """Set your RSVP, or clear it with `status=None`. `person_id` is the
        person acting; nobody can RSVP for someone else. `origin` is where
        the person is right now, if they chose to share it."""
        person = self._store.node(person_id, Person)
        event = self._visible_event(event_id, person_id)
        state = event.status_at(now)
        if state is EventStatus.ENDED:
            raise DomainRuleError("this event has ended, so RSVPs are frozen")
        if state is not EventStatus.PUBLISHED:
            raise DomainRuleError(f"a {state.value} event doesn't take RSVPs")
        existing = self.mine(event_id, person_id)
        if status is None:
            if existing is not None:
                self._store.delete_edge(existing.id)
            return None
        newly_going = status is RsvpStatus.GOING and not (
            existing and existing.status is RsvpStatus.GOING
        )
        if newly_going:
            self._require_room(event)
        if existing is not None:
            saved = self._store.save_edge(replace(existing, status=status, updated_at=now))
        else:
            saved = self._new_rsvp(person, event_id, status, now)
        learn_trip = (
            newly_going
            and not saved.trip_recorded  # one trip per event, however often they flip
            and origin is not None
            and event.coordinates is not None
            and saved.recommendation_use is not RecommendationUse.NONE
        )
        if learn_trip:
            self._preferences.record_trip(
                person_id, origin.distance_km(event.coordinates)  # type: ignore[union-attr]
            )
            saved = self._store.save_edge(replace(saved, trip_recorded=True))
        return saved

    def _new_rsvp(
        self, person: Person, event_id: str, status: RsvpStatus, now: datetime
    ) -> Rsvp:
        return self._store.save_edge(
            Rsvp(
                source_id=person.id,
                target_id=event_id,
                status=status,
                updated_at=now,
                visibility=person.rsvp_visibility,
                provenance=Provenance(
                    created_by=person.id, method=ProvenanceMethod.SELF_CLAIM, created_at=now
                ),
            )
        )

    def mine(self, event_id: str, person_id: str) -> Rsvp | None:
        found = self._store.edges(Rsvp, source_id=person_id, target_id=event_id)
        return found[0] if found else None

    def set_rsvp_visibility(
        self, person_id: str, by_person_id: str, level: Visibility
    ) -> Person:
        """Who can see your RSVPs: NETWORK, CONNECTIONS or PRIVATE (nobody).
        A platform admin may set it for someone else's account."""
        if not may_manage(self._store, person_id, by_person_id):
            raise PermissionDeniedError("only you can change who sees your RSVPs")
        if level not in RSVP_AUDIENCES:
            raise DomainRuleError(f"{level.name} isn't a choice for RSVPs")
        person = self._store.node(person_id, Person)
        with self._store.transaction():
            updated = self._store.save_node(replace(person, rsvp_visibility=level))
            for edge in self._store.edges(Rsvp, source_id=person_id):
                self._store.save_edge(edge.with_visibility(level))
        return updated

    # --- what others may see -------------------------------------------------

    def friends_going(
        self,
        viewer_id: str | None,
        event_id: str,
        *,
        friend_ids: set[str] | None = None,
    ) -> tuple[Person, ...]:
        """Your connections who are going and let you see it. Pass
        `friend_ids` to reuse a viewer's connections across many events."""
        if viewer_id is None:
            return ()
        friends = (
            friend_ids
            if friend_ids is not None
            else set(self._connections.connections_of(viewer_id))
        )
        going = [
            self._store.node(edge.source_id, Person)
            for edge in self._store.edges(Rsvp, target_id=event_id)
            if edge.status is RsvpStatus.GOING
            and edge.source_id in friends
            and edge.recommendation_use is not RecommendationUse.NONE  # their choice
            and self._privacy.can_view(viewer_id, edge)
        ]
        return tuple(sorted(going, key=lambda p: (p.display_name, p.id)))

    def is_full(self, event_id: str) -> bool:
        """At capacity: "going" is closed (spec 4.6)."""
        event = self._store.node(event_id, Event)
        return event.capacity is not None and self._going(event_id) >= event.capacity

    def going_count(self, event_id: str) -> int | None:
        """Everyone going, or None while it's under MIN_PUBLIC_COUNT."""
        count = self._going(event_id)
        return count if count >= MIN_PUBLIC_COUNT else None

    # --- internals -----------------------------------------------------------

    def _going(self, event_id: str) -> int:
        return sum(
            e.status is RsvpStatus.GOING for e in self._store.edges(Rsvp, target_id=event_id)
        )

    def _require_room(self, event: Event) -> None:
        if event.capacity is not None and self._going(event.id) >= event.capacity:
            raise DomainRuleError("this event is full")

    def _visible_event(self, event_id: str, viewer_id: str) -> Event:
        try:
            event = self._store.node(event_id, Event)
        except NotFoundError:
            event = None
        if event is None or not self._privacy.can_view_listing(viewer_id, event):
            raise NotFoundError(f"event {event_id!r} not found")
        return event


def may_manage(store: GraphStore, person_id: str, by_person_id: str) -> bool:
    """May `by_person_id` change this account's own settings? Only the
    person themselves, or a platform admin (Collectivize staff)."""
    if person_id == by_person_id:
        return True
    return store.has_node(by_person_id) and store.node(by_person_id, Person).is_platform_admin
