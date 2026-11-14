"""Stars: a person's private bookmarks on events.

- You can star an event you're allowed to see that is published (or
  cancelled) and hasn't ended. Starring twice changes nothing.
- Unstarring always works, even after the event changed, and removes the
  star completely (no record is kept).
- A star is private to its owner: it is never counted or shown to anyone
  else. It puts the event on the person's own calendar.
"""

from __future__ import annotations

from datetime import datetime

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    Person,
    Provenance,
    ProvenanceMethod,
    Starred,
)

from .errors import NotFoundError
from .privacy import PrivacyService
from .store import GraphStore


class StarService:
    def __init__(self, store: GraphStore, privacy: PrivacyService) -> None:
        self._store = store
        self._privacy = privacy

    def star(self, event_id: str, person_id: str, now: datetime) -> Starred:
        """Bookmark an event (idempotent)."""
        self._store.node(person_id, Person)
        event = self._visible_event(event_id, person_id)
        if not event.was_published or event.status is EventStatus.ARCHIVED:
            raise DomainRuleError("only listed events can be starred")
        if event.has_ended(now):
            raise DomainRuleError("this event has ended")
        existing = self.star_of(event_id, person_id)
        if existing is not None:
            return existing
        return self._store.save_edge(
            Starred(
                source_id=person_id,
                target_id=event_id,
                starred_at=now,
                provenance=Provenance(
                    created_by=person_id, method=ProvenanceMethod.SELF_CLAIM, created_at=now
                ),
            )
        )

    def unstar(self, event_id: str, person_id: str) -> bool:
        """Remove a bookmark. True if there was one."""
        found = self._store.edges(Starred, source_id=person_id, target_id=event_id)
        for edge in found:
            self._store.delete_edge(edge.id)
        return bool(found)

    def star_of(self, event_id: str, person_id: str) -> Starred | None:
        """A person's star on an event, if they starred it."""
        found = self._store.edges(Starred, source_id=person_id, target_id=event_id)
        return found[0] if found else None

    def starred_event_ids(self, person_id: str | None) -> set[str]:
        """Ids of the events a person has starred (empty for logged-out visitors)."""
        if person_id is None:
            return set()
        return {e.target_id for e in self._store.edges(Starred, source_id=person_id)}

    def _visible_event(self, event_id: str, viewer_id: str) -> Event:
        node = self._store.any_node(event_id) if self._store.has_node(event_id) else None
        if not isinstance(node, Event) or not self._privacy.can_view_listing(viewer_id, node):
            raise NotFoundError(f"event {event_id!r} not found")
        return node
