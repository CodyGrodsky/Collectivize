"""Asking to co-host someone else's event, and seeing what's waiting.

The other direction, a host inviting a co-host, lives in EventService
(`invite_co_host`). Both kinds of pending link are approved with
`EventService.accept_co_host` and declined or withdrawn with
`EventService.remove_co_host`; the link's `origin` decides who must
approve it.

Rules for a request to join:
- You ask for yourself, or for an org you are an admin of.
- You can only ask about an event you are allowed to see. Anything else
  looks exactly like a missing event, so requests can't be used to probe
  for hidden events.
- Only published (upcoming or in-progress) events take requests.
- One pending or accepted link per party per event.
"""

from __future__ import annotations

from datetime import datetime

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    HostOrigin,
    HostRole,
    Hosts,
    Provenance,
    ProvenanceMethod,
)

from .errors import NotFoundError, PermissionDeniedError
from .events import EventService
from .privacy import PrivacyService
from .store import GraphStore


class CoHostingService:
    def __init__(
        self, store: GraphStore, events: EventService, privacy: PrivacyService
    ) -> None:
        self._store = store
        self._events = events
        self._privacy = privacy

    def request(
        self, event_id: str, host_id: str, by_person_id: str, now: datetime
    ) -> Hosts:
        """`host_id` (the asker themselves, or an org they admin) asks to
        co-host. It counts only once a current host approves."""
        if not self._events.acts_for(by_person_id, host_id):
            raise PermissionDeniedError("you can only ask for yourself or an org you admin")
        event = self._visible_event(event_id, by_person_id)
        if event.status_at(now) is not EventStatus.PUBLISHED:
            raise DomainRuleError("only upcoming, published events take co-host requests")
        if self._store.edges(Hosts, source_id=host_id, target_id=event_id):
            raise DomainRuleError("already a host, invited, or waiting on a request")
        method = (
            ProvenanceMethod.SELF_CLAIM if host_id == by_person_id
            else ProvenanceMethod.ORG_ADMIN
        )
        edge = Hosts(
            source_id=host_id,
            target_id=event_id,
            role=HostRole.CO_HOST,
            origin=HostOrigin.REQUESTED,
            provenance=Provenance(created_by=by_person_id, method=method, created_at=now),
        )
        return self._store.save_edge(edge)

    def pending(self, event_id: str, by_person_id: str) -> list[Hosts]:
        """Invitations and requests on an event still awaiting an answer.
        Only the event's hosts can see them."""
        if not self._events.can_edit(by_person_id, event_id):
            raise PermissionDeniedError("only the event's hosts can see this")
        return [h for h in self._store.edges(Hosts, target_id=event_id) if not h.accepted]

    def pending_for(self, host_id: str, by_person_id: str) -> list[Hosts]:
        """What a person or org has been invited to, or has asked to join,
        that is still waiting. Only that party can see it."""
        if not self._events.acts_for(by_person_id, host_id):
            raise PermissionDeniedError("you can only see your own invitations and requests")
        return [h for h in self._store.edges(Hosts, source_id=host_id) if not h.accepted]

    def _visible_event(self, event_id: str, viewer_id: str) -> Event:
        try:
            event = self._store.node(event_id, Event)
        except NotFoundError:
            event = None
        if event is None or not event.is_active or not self._privacy.can_view(viewer_id, event):
            raise NotFoundError(f"event {event_id!r} not found")
        return event
