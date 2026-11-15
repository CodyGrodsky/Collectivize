"""CONNECTED rules: a person-to-person connection exists only when both
people agree, and either can remove it silently.

- One person sends a request (the edge's source); only the recipient
  (the target) can accept or decline it.
- At most one connection edge per pair, in either direction.
- Declining or removing hard-deletes the edge, so no record remains that
  the two people were ever linked.
- Nobody can have more than MAX_OPEN_REQUESTS requests waiting for an
  answer at once, so one account can't send requests to everyone.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.objects import (
    Connected,
    ConnectionOrigin,
    DomainRuleError,
    Person,
    Provenance,
    ProvenanceMethod,
)

from .errors import PermissionDeniedError
from .store import GraphStore

MAX_OPEN_REQUESTS = 25  # requests one person may have waiting for an answer


class ConnectionService:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    # --- queries -------------------------------------------------------------

    def between(self, a: str, b: str) -> Connected | None:
        """The edge (pending or established) linking two people, if any."""
        for source, target in ((a, b), (b, a)):
            found = self._store.edges(Connected, source_id=source, target_id=target)
            if found:
                return found[0]
        return None

    def are_connected(self, a: str, b: str) -> bool:
        edge = self.between(a, b)
        return edge is not None and edge.is_established

    def connections_of(self, person_id: str) -> list[str]:
        """Ids of people this person is mutually connected with."""
        edges = self._store.edges(Connected, source_id=person_id) + self._store.edges(
            Connected, target_id=person_id
        )
        return [
            e.target_id if e.source_id == person_id else e.source_id
            for e in edges
            if e.is_established
        ]

    # --- commands ------------------------------------------------------------

    def request(self, from_person_id: str, to_person_id: str, now: datetime) -> Connected:
        self._store.node(from_person_id, Person)
        self._store.node(to_person_id, Person)
        if from_person_id == to_person_id:
            raise DomainRuleError("you cannot connect with yourself")
        if self.between(from_person_id, to_person_id) is not None:
            raise DomainRuleError("a connection or request already exists")
        waiting = sum(not edge.is_established
                      for edge in self._store.edges(Connected, source_id=from_person_id))
        if waiting >= MAX_OPEN_REQUESTS:
            raise DomainRuleError(
                f"you have {waiting} connection requests waiting for an answer: "
                "wait for some replies, or cancel a few, before sending more"
            )
        edge = Connected(
            source_id=from_person_id,
            target_id=to_person_id,
            source_accepted=True,
            provenance=Provenance(
                created_by=from_person_id,
                method=ProvenanceMethod.SELF_CLAIM,
                created_at=now,
            ),
        )
        return self._store.save_edge(edge)

    def connect_via_invite(
        self, inviter_id: str, invitee_id: str, now: datetime
    ) -> Connected:
        """The inviter agreed by sending the invite and the invitee by
        redeeming it, so the connection starts out accepted by both. Only
        InviteService calls this, after checking the invite."""
        if self.between(inviter_id, invitee_id) is not None:
            raise DomainRuleError("a connection or request already exists")
        edge = Connected(
            source_id=inviter_id,
            target_id=invitee_id,
            source_accepted=True,
            target_accepted=True,
            origin=ConnectionOrigin.INVITE,
            provenance=Provenance(
                created_by=inviter_id, method=ProvenanceMethod.SELF_CLAIM, created_at=now
            ),
        )
        return self._store.save_edge(edge)

    def accept(self, edge_id: str, by_person_id: str) -> Connected:
        edge = self._store.edge(edge_id, Connected)
        if by_person_id != edge.target_id:
            raise PermissionDeniedError("only the person who was asked can accept")
        return self._store.save_edge(replace(edge, target_accepted=True))

    def set_hide_connections(
        self, person_id: str, by_person_id: str, hidden: bool
    ) -> Person:
        """Hide or show your connection list. Only you can change it."""
        if person_id != by_person_id:
            raise PermissionDeniedError("only you can change who sees your connections")
        person = self._store.node(person_id, Person)
        return self._store.save_node(person.with_hide_connections(hidden))

    def remove(self, edge_id: str, by_person_id: str) -> None:
        """Decline a request or end a connection. Either person may do it."""
        edge = self._store.edge(edge_id, Connected)
        if by_person_id not in (edge.source_id, edge.target_id):
            raise PermissionDeniedError("only the two people involved can remove this")
        self._store.delete_edge(edge.id)
