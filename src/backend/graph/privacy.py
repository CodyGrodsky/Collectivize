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

from backend.objects import (
    Edge,
    Event,
    GraphRecord,
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
        level = self.effective_visibility(record)
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
        were primary host of pass to a co-host or are deleted."""
        self._store.node(person_id, Person)
        with self._store.transaction():
            self._events.release_host(person_id)
            for edge in self._store.edges_touching(person_id):
                if isinstance(edge, MemberOf):
                    self._membership.release_if_last_admin(edge)
                self._store.delete_edge(edge.id)
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
