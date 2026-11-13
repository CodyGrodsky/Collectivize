"""PART_OF rules: mutual acceptance (or staff verification), allowed
kinds per org type, multiple parents, and no cycles."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.objects import (
    DomainRuleError,
    Organization,
    OrgKind,
    PartOf,
    PartOfKind,
    Provenance,
    ProvenanceMethod,
    VerificationStatus,
)

from .errors import PermissionDeniedError
from .membership import MembershipService
from .store import GraphStore

# kind -> (allowed child org kinds, allowed parent org kinds)
ALLOWED_PART_OF: dict[PartOfKind, tuple[frozenset[OrgKind], frozenset[OrgKind]]] = {
    PartOfKind.CHAPTER_OF: (frozenset({OrgKind.CHAPTER}), frozenset({OrgKind.NATIONAL})),
    PartOfKind.MEMBER_OF_COUNCIL: (
        frozenset({OrgKind.CHAPTER, OrgKind.CLUB}),
        frozenset({OrgKind.COUNCIL}),
    ),
    PartOfKind.UNDER_INSTITUTION: (
        frozenset({OrgKind.CHAPTER, OrgKind.COUNCIL, OrgKind.CLUB, OrgKind.NATIONAL}),
        frozenset({OrgKind.INSTITUTION}),
    ),
}


def check_part_of_kinds(child: Organization, parent: Organization, kind: PartOfKind) -> None:
    child_kinds, parent_kinds = ALLOWED_PART_OF[kind]
    if child.kind not in child_kinds or parent.kind not in parent_kinds:
        raise DomainRuleError(
            f"a {child.kind.value} cannot be {kind.value} a {parent.kind.value}"
        )


class HierarchyService:
    def __init__(self, store: GraphStore, membership: MembershipService) -> None:
        self._store = store
        self._membership = membership

    # --- queries -------------------------------------------------------------

    def parents(self, org_id: str) -> list[Organization]:
        return [
            self._store.node(e.target_id, Organization)
            for e in self._store.edges(PartOf, source_id=org_id)
            if e.is_established
        ]

    def children(self, org_id: str) -> list[Organization]:
        return [
            self._store.node(e.source_id, Organization)
            for e in self._store.edges(PartOf, target_id=org_id)
            if e.is_established
        ]

    def descendant_ids(self, org_id: str) -> set[str]:
        """Established descendants, e.g. every chapter under a council."""
        seen: set[str] = set()
        frontier = [org_id]
        while frontier:
            current = frontier.pop()
            for edge in self._store.edges(PartOf, target_id=current):
                if edge.is_established and edge.source_id not in seen:
                    seen.add(edge.source_id)
                    frontier.append(edge.source_id)
        return seen

    def ancestor_ids(self, org_id: str, *, include_pending: bool = False) -> set[str]:
        seen: set[str] = set()
        frontier = [org_id]
        while frontier:
            current = frontier.pop()
            for edge in self._store.edges(PartOf, source_id=current):
                if not (include_pending or edge.is_established):
                    continue
                if edge.target_id not in seen:
                    seen.add(edge.target_id)
                    frontier.append(edge.target_id)
        return seen

    # --- commands ------------------------------------------------------------

    def propose(
        self,
        child_id: str,
        parent_id: str,
        kind: PartOfKind,
        by_person_id: str,
        now: datetime,
    ) -> PartOf:
        """An admin of either side proposes; that side is auto-accepted."""
        is_child_admin = self._membership.is_admin(by_person_id, child_id)
        is_parent_admin = self._membership.is_admin(by_person_id, parent_id)
        if not (is_child_admin or is_parent_admin):
            raise PermissionDeniedError("only an admin of either org can propose this")
        edge = self._new_edge(
            child_id, parent_id, kind, by_person_id, ProvenanceMethod.ORG_ADMIN, now
        )
        accepted = replace(
            edge, child_accepted=is_child_admin, parent_accepted=is_parent_admin
        )
        return self._store.save_edge(accepted)

    def accept(self, edge_id: str, by_person_id: str) -> PartOf:
        edge = self._store.edge(edge_id, PartOf)
        is_child_admin = self._membership.is_admin(by_person_id, edge.source_id)
        is_parent_admin = self._membership.is_admin(by_person_id, edge.target_id)
        if not (is_child_admin or is_parent_admin):
            raise PermissionDeniedError("only an admin of either org can accept")
        accepted = replace(
            edge,
            child_accepted=edge.child_accepted or is_child_admin,
            parent_accepted=edge.parent_accepted or is_parent_admin,
        )
        return self._store.save_edge(accepted)

    def staff_link(
        self,
        child_id: str,
        parent_id: str,
        kind: PartOfKind,
        staff_id: str,
        now: datetime,
    ) -> PartOf:
        """Staff verified the relation against a public source."""
        edge = self._new_edge(
            child_id, parent_id, kind, staff_id, ProvenanceMethod.STAFF, now
        )
        return self._store.save_edge(self._verified(edge, now))

    def staff_verify(self, edge_id: str, now: datetime) -> PartOf:
        edge = self._store.edge(edge_id, PartOf)
        return self._store.save_edge(self._verified(edge, now))

    # --- internals -----------------------------------------------------------

    def _new_edge(
        self,
        child_id: str,
        parent_id: str,
        kind: PartOfKind,
        created_by: str,
        method: ProvenanceMethod,
        now: datetime,
    ) -> PartOf:
        child = self._store.node(child_id, Organization)
        parent = self._store.node(parent_id, Organization)
        check_part_of_kinds(child, parent, kind)
        if any(e.target_id == parent_id for e in self._store.edges(PartOf, source_id=child_id)):
            raise DomainRuleError("this relationship already exists")
        if child_id in self.ancestor_ids(parent_id, include_pending=True):
            raise DomainRuleError("an org cannot become its own ancestor")
        return PartOf(
            source_id=child_id,
            target_id=parent_id,
            kind=kind,
            provenance=Provenance(created_by=created_by, method=method, created_at=now),
        )

    @staticmethod
    def _verified(edge: PartOf, now: datetime) -> PartOf:
        provenance = replace(
            edge.provenance,
            last_confirmed_at=now,
            verification=VerificationStatus.VERIFIED,
        )
        return replace(edge, staff_verified=True, provenance=provenance)
