"""Integrity audit: checks that every relationship stored in the graph is
genuine, i.e. it connects the right kinds of node and carries evidence
that the right parties agreed to it.

The services already refuse to create bad edges. The audit exists for
everything else: data written before a rule existed, data written by an
import script or by hand, or a bug. It only reads; it never repairs.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.objects import (
    ERASED_CONFIRMER,
    ClaimState,
    Connected,
    DomainRuleError,
    Edge,
    Event,
    EventStatus,
    GraphRecord,
    HostRole,
    Hosts,
    MemberOf,
    MemberRole,
    Organization,
    PartOf,
    VerificationStatus,
)

from .hierarchy import check_part_of_kinds
from .store import GraphStore

HOSTED_STATUSES = frozenset({EventStatus.PUBLISHED, EventStatus.CANCELLED})


@dataclass(frozen=True)
class IntegrityIssue:
    record_id: str
    problem: str


class IntegrityService:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    def audit(self) -> tuple[IntegrityIssue, ...]:
        edges = self._store.edges(Edge, include_inactive=True)
        typed = [e for e in edges if self._endpoint_issue(e) is None]
        issues = [
            IntegrityIssue(e.id, problem)
            for e in edges
            if (problem := self._endpoint_issue(e)) is not None
        ]
        issues += self._membership_issues([e for e in typed if isinstance(e, MemberOf)])
        issues += self._claim_issues()
        issues += self._connection_issues([e for e in typed if isinstance(e, Connected)])
        issues += self._hierarchy_issues([e for e in typed if isinstance(e, PartOf)])
        issues += self._hosting_issues()
        return tuple(issues)

    # --- checks --------------------------------------------------------------

    def _endpoint_issue(self, edge: Edge) -> str | None:
        source = self._find(edge.source_id)
        target = self._find(edge.target_id)
        if source is None or target is None:
            return "edge points at a node that no longer exists"
        try:
            edge.check_endpoints(source, target)
        except DomainRuleError as error:
            return str(error)
        return None

    def _membership_issues(self, memberships: list[MemberOf]) -> list[IntegrityIssue]:
        issues = []
        for edge in memberships:
            if not edge.org_confirmed:
                continue
            staff_approved = edge.provenance.verification is VerificationStatus.VERIFIED
            if not (staff_approved or self._was_admin(edge.confirmed_by, edge.target_id)):
                issues.append(IntegrityIssue(
                    edge.id, "membership was confirmed by someone who is not an admin "
                    "of the org and was not approved by staff",
                ))
        return issues

    def _claim_issues(self) -> list[IntegrityIssue]:
        issues = []
        for org in self._store.nodes(Organization):
            admins = [m for m in self._store.edges(MemberOf, target_id=org.id) if m.is_admin]
            if org.claim_state is ClaimState.CLAIMED and not admins:
                issues.append(IntegrityIssue(org.id, "org is claimed but has no admin"))
            if org.claim_state is not ClaimState.CLAIMED:
                issues += [
                    IntegrityIssue(m.id, "person is an admin of an unclaimed org")
                    for m in admins
                ]
        return issues

    def _connection_issues(self, connections: list[Connected]) -> list[IntegrityIssue]:
        return [
            IntegrityIssue(e.id, "connection that neither person requested")
            for e in connections
            if e.provenance.created_by not in (e.source_id, e.target_id)
        ]

    def _hierarchy_issues(self, links: list[PartOf]) -> list[IntegrityIssue]:
        issues = []
        for edge in links:
            child = self._store.node(edge.source_id, Organization)
            parent = self._store.node(edge.target_id, Organization)
            try:
                check_part_of_kinds(child, parent, edge.kind)
            except DomainRuleError as error:
                issues.append(IntegrityIssue(edge.id, str(error)))
        return issues

    def _hosting_issues(self) -> list[IntegrityIssue]:
        issues = []
        for event in self._store.nodes(Event):
            if event.status not in HOSTED_STATUSES:
                continue
            primaries = [
                h for h in self._store.edges(Hosts, target_id=event.id)
                if h.is_effective and h.role is HostRole.PRIMARY
            ]
            if len(primaries) != 1:
                issues.append(IntegrityIssue(
                    event.id, "listed event does not have exactly one primary host"
                ))
        return issues

    # --- internals -----------------------------------------------------------

    def _find(self, node_id: str) -> GraphRecord | None:
        return self._store.any_node(node_id) if self._store.has_node(node_id) else None

    def _was_admin(self, person_id: str | None, org_id: str) -> bool:
        """Is, or ever was, a confirmed admin of the org."""
        if person_id is None:
            return False
        if person_id == ERASED_CONFIRMER:
            return True  # confirmed by an admin whose account was since erased
        return any(
            m.org_confirmed and (m.role is MemberRole.ADMIN or m.was_admin)
            for m in self._store.edges(
                MemberOf, source_id=person_id, target_id=org_id, include_inactive=True
            )
        )
