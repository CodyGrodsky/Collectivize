"""HOSTS and TAGGED rules plus the event lifecycle.

- Exactly one primary host; co-hosts must accept before they count.
- Only admins of an effective host org may edit an event.
- A co-host can remove itself at any time.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    HostRole,
    Hosts,
    Organization,
    Provenance,
    ProvenanceMethod,
    Tag,
    Tagged,
)

from .errors import PermissionDeniedError
from .membership import MembershipService
from .store import GraphStore


class EventService:
    def __init__(self, store: GraphStore, membership: MembershipService) -> None:
        self._store = store
        self._membership = membership

    # --- queries -------------------------------------------------------------

    def host_edges(self, event_id: str) -> list[Hosts]:
        return [h for h in self._store.edges(Hosts, target_id=event_id) if h.is_effective]

    def primary_host_id(self, event_id: str) -> str:
        primaries = [h for h in self.host_edges(event_id) if h.role is HostRole.PRIMARY]
        if len(primaries) != 1:
            raise DomainRuleError("event must have exactly one primary host")
        return primaries[0].source_id

    def host_org_ids(self, event_id: str) -> set[str]:
        return {h.source_id for h in self.host_edges(event_id)}

    def can_edit(self, person_id: str, event_id: str) -> bool:
        return any(
            self._membership.is_admin(person_id, org_id)
            for org_id in self.host_org_ids(event_id)
        )

    # --- lifecycle -----------------------------------------------------------

    def create(self, event: Event, host_org_id: str, by_person_id: str) -> Event:
        self._membership.require_admin(by_person_id, host_org_id)
        if event.status is not EventStatus.DRAFT:
            raise DomainRuleError("new events start as drafts")
        self._store.save_node(event)
        self._store.save_edge(
            Hosts(
                source_id=host_org_id,
                target_id=event.id,
                role=HostRole.PRIMARY,
                accepted=True,
                provenance=self._provenance(by_person_id, event.provenance.created_at),
            )
        )
        return event

    def publish(self, event_id: str, by_person_id: str, now: datetime) -> Event:
        event = self._editable(event_id, by_person_id)
        published = self._store.save_node(event.published(now))
        org = self._store.node(self.primary_host_id(event_id), Organization)
        self._store.save_node(org.with_event_published(now))
        return published

    def revise(
        self, event_id: str, by_person_id: str, now: datetime, **changes: Any
    ) -> Event:
        event = self._editable(event_id, by_person_id)
        return self._store.save_node(event.revised(now, **changes))

    def cancel(self, event_id: str, by_person_id: str) -> Event:
        event = self._editable(event_id, by_person_id)
        return self._store.save_node(event.cancelled())

    def archive(self, event_id: str, by_person_id: str) -> Event:
        event = self._editable(event_id, by_person_id)
        return self._store.save_node(event.archived())

    # --- co-hosting ----------------------------------------------------------

    def invite_co_host(
        self, event_id: str, org_id: str, by_person_id: str, now: datetime
    ) -> Hosts:
        self._editable(event_id, by_person_id)
        self._store.node(org_id, Organization)
        if any(h.source_id == org_id for h in self._store.edges(Hosts, target_id=event_id)):
            raise DomainRuleError("org is already a host or invited")
        edge = Hosts(
            source_id=org_id,
            target_id=event_id,
            role=HostRole.CO_HOST,
            provenance=self._provenance(by_person_id, now),
        )
        return self._store.save_edge(edge)

    def accept_co_host(self, edge_id: str, by_person_id: str) -> Hosts:
        edge = self._store.edge(edge_id, Hosts)
        self._membership.require_admin(by_person_id, edge.source_id)
        return self._store.save_edge(replace(edge, accepted=True))

    def remove_co_host(self, edge_id: str, by_person_id: str) -> Hosts:
        edge = self._store.edge(edge_id, Hosts)
        if edge.role is HostRole.PRIMARY:
            raise DomainRuleError("the primary host cannot be removed")
        is_self = self._membership.is_admin(by_person_id, edge.source_id)
        is_primary = self._membership.is_admin(
            by_person_id, self.primary_host_id(edge.target_id)
        )
        if not (is_self or is_primary):
            raise PermissionDeniedError("only the co-host or primary host can remove it")
        return self._store.save_edge(edge.removed())

    # --- tagging -------------------------------------------------------------

    def tag(self, subject_id: str, tag_id: str, by_person_id: str, now: datetime) -> Tagged:
        """Orgs and events are tagged by their admins."""
        self._store.node(tag_id, Tag)
        subject = self._store.any_node(subject_id)
        if isinstance(subject, Organization):
            self._membership.require_admin(by_person_id, subject_id)
        elif isinstance(subject, Event):
            self._editable(subject_id, by_person_id)
        else:
            raise DomainRuleError("only orgs and events can be tagged")
        existing = self._store.edges(Tagged, source_id=subject_id, target_id=tag_id)
        if existing:
            return existing[0]
        edge = Tagged(
            source_id=subject_id,
            target_id=tag_id,
            provenance=self._provenance(by_person_id, now),
        )
        return self._store.save_edge(edge)

    # --- internals -----------------------------------------------------------

    def _editable(self, event_id: str, by_person_id: str) -> Event:
        event = self._store.node(event_id, Event)
        if not self.can_edit(by_person_id, event_id):
            raise PermissionDeniedError("only admins of a host org can edit this event")
        return event

    @staticmethod
    def _provenance(by_person_id: str, at: datetime) -> Provenance:
        return Provenance(
            created_by=by_person_id, method=ProvenanceMethod.ORG_ADMIN, created_at=at
        )

