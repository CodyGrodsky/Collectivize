"""HOSTS and TAGGED rules plus the event lifecycle.

- A host is either a person or an org. A person acts for themselves; an
  org acts through its admins.
- Exactly one primary host. Combined events: an existing host invites
  another person or org, and that party must accept before it counts.
  An event can have any number of co-hosts.
- Anyone acting for an accepted host may edit the event.
- A co-host can decline or leave at any time; the primary can remove it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    HostOrigin,
    HostRole,
    Hosts,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    Tag,
    Tagged,
)

from .errors import NotFoundError, PermissionDeniedError
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

    def host_ids(self, event_id: str) -> set[str]:
        """Every accepted host, people and orgs."""
        return {h.source_id for h in self.host_edges(event_id)}

    def host_org_ids(self, event_id: str) -> set[str]:
        return {
            host_id
            for host_id in self.host_ids(event_id)
            if isinstance(self._store.any_node(host_id), Organization)
        }

    def acts_for(self, person_id: str, host_id: str) -> bool:
        """A person acts for themselves, and for orgs they are an admin of."""
        host = self.host_node(host_id)
        if isinstance(host, Person):
            return host.id == person_id
        return self._membership.is_admin(person_id, host_id)

    def can_edit(self, person_id: str, event_id: str) -> bool:
        return any(self.acts_for(person_id, h) for h in self.host_ids(event_id))

    def host_node(self, host_id: str) -> Person | Organization:
        node = self._store.any_node(host_id)
        if not isinstance(node, (Person, Organization)):
            raise NotFoundError(f"host {host_id!r} must be a person or an org")
        return node

    # --- lifecycle -----------------------------------------------------------

    def create(self, event: Event, host_id: str, by_person_id: str) -> Event:
        """`host_id` is the primary host: the creator themselves, or an org
        the creator is an admin of."""
        self._require_acts_for(by_person_id, host_id)
        if event.status is not EventStatus.DRAFT:
            raise DomainRuleError("new events start as drafts")
        self._store.save_node(event)
        self._store.save_edge(
            Hosts(
                source_id=host_id,
                target_id=event.id,
                role=HostRole.PRIMARY,
                accepted=True,
                provenance=self._provenance(
                    by_person_id, event.provenance.created_at, host_id
                ),
            )
        )
        return event

    def publish(self, event_id: str, by_person_id: str, now: datetime) -> Event:
        event = self._editable(event_id, by_person_id)
        published = self._store.save_node(event.published(now))
        host = self.host_node(self.primary_host_id(event_id))
        if isinstance(host, Organization):
            self._store.save_node(host.with_event_published(now))
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
        self, event_id: str, host_id: str, by_person_id: str, now: datetime
    ) -> Hosts:
        """An existing host asks a person or org to co-host. Nothing changes
        for the invited party until it accepts."""
        self._editable(event_id, by_person_id)
        self.host_node(host_id)
        if any(h.source_id == host_id for h in self._store.edges(Hosts, target_id=event_id)):
            raise DomainRuleError("already a host or invited")
        edge = Hosts(
            source_id=host_id,
            target_id=event_id,
            role=HostRole.CO_HOST,
            provenance=self._provenance(by_person_id, now),
        )
        return self._store.save_edge(edge)

    def accept_co_host(self, edge_id: str, by_person_id: str) -> Hosts:
        """Approve a pending co-host link. An invitation is approved by the
        invited person (or an admin of the invited org); a request to join
        is approved by anyone acting for a current host of the event."""
        edge = self._store.edge(edge_id, Hosts)
        if edge.origin is HostOrigin.REQUESTED:
            if not self.can_edit(by_person_id, edge.target_id):
                raise PermissionDeniedError("only the event's hosts can approve a request")
        else:
            self._require_acts_for(by_person_id, edge.source_id)
        return self._store.save_edge(replace(edge, accepted=True))

    def remove_co_host(self, edge_id: str, by_person_id: str) -> Hosts:
        """Decline, withdraw, leave or remove. The co-host itself and the
        primary host can always do it; any current host can also decline
        a pending request to join."""
        edge = self._store.edge(edge_id, Hosts)
        if edge.role is HostRole.PRIMARY:
            raise DomainRuleError("the primary host cannot be removed")
        is_self = self.acts_for(by_person_id, edge.source_id)
        is_primary = self.acts_for(by_person_id, self.primary_host_id(edge.target_id))
        declines_request = (
            edge.origin is HostOrigin.REQUESTED
            and not edge.accepted
            and self.can_edit(by_person_id, edge.target_id)
        )
        if not (is_self or is_primary or declines_request):
            raise PermissionDeniedError("only the co-host or primary host can remove it")
        return self._store.save_edge(edge.removed())

    def release_host(self, host_id: str) -> None:
        """Called when a host's account is erased. Events they were primary
        host of pass to the earliest accepted co-host, or are deleted when
        nobody else is hosting them."""
        for edge in self._store.edges(Hosts, source_id=host_id):
            if edge.role is not HostRole.PRIMARY:
                continue
            co_hosts = [h for h in self.host_edges(edge.target_id) if h.id != edge.id]
            if co_hosts:
                self._store.save_edge(replace(co_hosts[0], role=HostRole.PRIMARY))
            else:
                self._delete_event(edge.target_id)

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
            raise PermissionDeniedError("only the event's hosts can edit it")
        return event

    def _require_acts_for(self, person_id: str, host_id: str) -> None:
        if not self.acts_for(person_id, host_id):
            raise PermissionDeniedError("you can only act for yourself or an org you admin")

    def _delete_event(self, event_id: str) -> None:
        for edge in self._store.edges_touching(event_id):
            self._store.delete_edge(edge.id)
        self._store.delete_node(event_id)

    @staticmethod
    def _provenance(
        by_person_id: str, at: datetime, host_id: str | None = None
    ) -> Provenance:
        method = (
            ProvenanceMethod.SELF_CLAIM if host_id == by_person_id
            else ProvenanceMethod.ORG_ADMIN
        )
        return Provenance(created_by=by_person_id, method=method, created_at=at)

