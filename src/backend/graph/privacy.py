"""Visibility enforcement and account erasure.

- An edge's effective visibility is the most restrictive of its own level
  and both endpoints', so no party can expose another more than they chose.
- Events that were never published (drafts, and drafts cancelled before
  publishing) are visible only to the people who can edit them.
- Deleting an account truly erases the person's edges (not just hides them).
- People who host an event own it: they can always see it, and a
  connections-only event is visible to their connections.
"""

from __future__ import annotations

from dataclasses import replace

from backend.objects import (
    ERASED_CONFIRMER,
    DomainRuleError,
    Edge,
    Event,
    GraphRecord,
    Invite,
    MemberOf,
    Organization,
    Person,
    Visibility,
    most_restrictive,
)

from .connections import ConnectionService
from .events import EventService
from .membership import MembershipService
from .store import GraphStore


class PrivacyService:
    def __init__(
        self,
        store: GraphStore,
        membership: MembershipService,
        events: EventService,
        connections: ConnectionService,
    ) -> None:
        self._store = store
        self._membership = membership
        self._events = events
        self._connections = connections

    def effective_visibility(self, record: GraphRecord) -> Visibility:
        if not isinstance(record, Edge):
            return record.visibility
        return most_restrictive(
            record.visibility,
            self._store.any_node(record.source_id).visibility,
            self._store.any_node(record.target_id).visibility,
        )

    def can_view(self, viewer_id: str | None, record: GraphRecord) -> bool:
        if isinstance(record, Event) and not record.was_published:
            return viewer_id is not None and self._events.can_edit(viewer_id, record.id)
        owners = self._owner_ids(record)
        if viewer_id is not None and viewer_id in owners:
            return True
        if (
            isinstance(record, Event)
            and viewer_id is not None
            and self._events.can_edit(viewer_id, record.id)
        ):
            return True  # its hosts (including an org's admins) always see it
        if isinstance(record, Edge) and not all(
            self.can_view(viewer_id, self._store.any_node(node_id))
            for node_id in (record.source_id, record.target_id)
        ):
            # Every party's setting must allow it, not just the strictest one:
            # "members only" and "connections only" are different groups, so a
            # connections-only RSVP to a members-only event needs both.
            return False
        return self._allows(viewer_id, record, record.visibility, owners)

    def can_view_listing(self, viewer_id: str | None, event: Event) -> bool:
        """Whether an event may be shown or acted on: the viewer must be able
        to see the event and its primary host (an event is never more visible
        than whoever runs it). Used by Discover, RSVPs and co-host requests."""
        if not (event.is_active and self.can_view(viewer_id, event)):
            return False
        try:
            host = self._store.any_node(self._events.primary_host_id(event.id))
        except (DomainRuleError, LookupError):
            return False
        return self.can_view(viewer_id, host)

    def _allows(
        self, viewer_id: str | None, record: GraphRecord, level: Visibility, owners: set[str]
    ) -> bool:
        if level is Visibility.PUBLIC:
            return True
        if viewer_id is None:
            return False
        if level is Visibility.NETWORK:
            return True
        if level is Visibility.MEMBERS:
            return any(
                self._membership.is_member(viewer_id, org_id)
                for org_id in self._related_org_ids(record)
            )
        if level is Visibility.CONNECTIONS:
            return any(self.are_connected(viewer_id, owner) for owner in owners)
        return False

    def are_connected(self, a: str, b: str) -> bool:
        return self._connections.are_connected(a, b)

    def erase_person(self, person_id: str) -> None:
        """True erasure: hard-delete every edge touching the person, then the
        node. Orgs left without an admin return to unclaimed; events they
        were primary host of pass to a co-host or are deleted; invites they
        sent or redeemed are deleted."""
        self._store.node(person_id, Person)
        with self._store.transaction():
            self._events.release_host(person_id)
            for edge in self._store.edges(MemberOf, include_inactive=True):
                if edge.confirmed_by == person_id and edge.source_id != person_id:
                    # Keep the fact that an admin confirmed it, not who.
                    self._store.save_edge(replace(edge, confirmed_by=ERASED_CONFIRMER))
            for edge in self._store.edges_touching(person_id):
                if isinstance(edge, MemberOf):
                    self._membership.release_if_last_admin(edge)
                self._store.delete_edge(edge.id)
            for invite in self._store.nodes(Invite):
                if person_id in (invite.inviter_id, invite.used_by):
                    self._store.delete_node(invite.id)
            self._store.delete_node(person_id)

    # --- internals -----------------------------------------------------------

    def _owner_ids(self, record: GraphRecord) -> set[str]:
        if isinstance(record, Person):
            return {record.id}
        if isinstance(record, Event):
            return {
                host_id
                for host_id in self._events.host_ids(record.id)
                if isinstance(self._store.any_node(host_id), Person)
            }
        if isinstance(record, Edge):
            return {
                node_id
                for node_id in (record.source_id, record.target_id)
                if isinstance(self._store.any_node(node_id), Person)
            }
        return set()

    def _related_org_ids(self, record: GraphRecord) -> set[str]:
        if isinstance(record, Organization):
            return {record.id}
        if isinstance(record, Event):
            return self._events.host_org_ids(record.id)
        if isinstance(record, Edge):
            return self._related_org_ids(
                self._store.any_node(record.source_id)
            ) | self._related_org_ids(self._store.any_node(record.target_id))
        return set()
