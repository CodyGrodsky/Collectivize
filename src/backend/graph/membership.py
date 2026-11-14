"""MEMBER_OF rules: two-sided membership, admin role, and org claiming.

`staff_id` arguments are trusted: the caller (auth layer) must have
already verified that the actor is Collectivize staff.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.objects import (
    ClaimState,
    DomainRuleError,
    MemberOf,
    MemberRole,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    VerificationStatus,
    Visibility,
)

from .errors import PermissionDeniedError
from .store import GraphStore


class MembershipService:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    # --- queries -------------------------------------------------------------

    def current(self, person_id: str, org_id: str) -> MemberOf | None:
        matches = [
            m
            for m in self._store.edges(MemberOf, source_id=person_id, target_id=org_id)
            if m.is_current
        ]
        return matches[0] if matches else None

    def is_member(self, person_id: str, org_id: str) -> bool:
        membership = self.current(person_id, org_id)
        return membership is not None and membership.org_confirmed

    def is_admin(self, person_id: str, org_id: str) -> bool:
        membership = self.current(person_id, org_id)
        return membership is not None and membership.is_admin

    def require_admin(self, person_id: str, org_id: str) -> None:
        if not self.is_admin(person_id, org_id):
            raise PermissionDeniedError("only an admin of this org can do that")

    def admins(self, org_id: str) -> list[MemberOf]:
        return [m for m in self._store.edges(MemberOf, target_id=org_id) if m.is_admin]

    # --- person-initiated ----------------------------------------------------

    def request(self, person_id: str, org_id: str, now: datetime) -> MemberOf:
        """The person claims membership; the org must still confirm it."""
        self._store.node(person_id, Person)
        self._store.node(org_id, Organization)
        if self.current(person_id, org_id) is not None:
            raise DomainRuleError("membership already exists")
        edge = MemberOf(
            source_id=person_id,
            target_id=org_id,
            started_at=now,
            provenance=Provenance(
                created_by=person_id, method=ProvenanceMethod.SELF_CLAIM, created_at=now
            ),
        )
        return self._store.save_edge(edge)

    def set_visibility(
        self, edge_id: str, by_person_id: str, visibility: Visibility
    ) -> MemberOf:
        """Only the person decides how visible their membership is."""
        edge = self._own_edge(edge_id, by_person_id)
        return self._store.save_edge(edge.with_visibility(visibility))

    def leave(
        self,
        edge_id: str,
        by_person_id: str,
        now: datetime,
        *,
        keep_visible: bool = True,
        release_org: bool = False,
    ) -> MemberOf:
        """End a membership. The last admin must hand off the role first or
        pass `release_org=True` to return the org to unclaimed."""
        edge = self._own_edge(edge_id, by_person_id)
        if not edge.is_current:
            raise DomainRuleError("membership has already ended")
        if edge.is_admin and len(self.admins(edge.target_id)) == 1:
            if not release_org:
                raise DomainRuleError(
                    "the last admin must hand off the role or release the org"
                )
            self._release_org(edge.target_id)
        ended = edge.ended(now)
        if not keep_visible:
            ended = ended.with_visibility(Visibility.PRIVATE)
        return self._store.save_edge(ended)

    # --- org-initiated -------------------------------------------------------

    def confirm(self, edge_id: str, by_person_id: str) -> MemberOf:
        edge = self._store.edge(edge_id, MemberOf)
        self.require_admin(by_person_id, edge.target_id)
        if not edge.is_current:
            raise DomainRuleError("an ended membership cannot be confirmed")
        return self._store.save_edge(edge.confirmed(by=by_person_id))

    def set_role(self, edge_id: str, role: MemberRole, by_person_id: str) -> MemberOf:
        """Only the org (an admin) can grant or change roles."""
        edge = self._store.edge(edge_id, MemberOf)
        self.require_admin(by_person_id, edge.target_id)
        if not edge.is_confirmed_current:
            raise DomainRuleError("only confirmed, current members can get a role")
        demoting_last_admin = (
            edge.is_admin
            and role is not MemberRole.ADMIN
            and len(self.admins(edge.target_id)) == 1
        )
        if demoting_last_admin:
            raise DomainRuleError("an org must always have at least one admin")
        return self._store.save_edge(edge.with_role(role))

    # --- claiming ------------------------------------------------------------

    def claim_org(self, person_id: str, org_id: str, now: datetime) -> MemberOf:
        """Request admin access to an unclaimed listing."""
        org = self._store.node(org_id, Organization)
        if org.claim_state is not ClaimState.UNCLAIMED:
            raise DomainRuleError("org is not open for claiming")
        edge = self.current(person_id, org_id) or self.request(person_id, org_id, now)
        edge = self._store.save_edge(edge.with_role(MemberRole.ADMIN))
        self._store.save_node(org.with_claim_state(ClaimState.CLAIM_PENDING))
        return edge

    def approve_claim(self, edge_id: str, staff_id: str, now: datetime) -> MemberOf:
        edge = self._store.edge(edge_id, MemberOf)
        org = self._store.node(edge.target_id, Organization)
        if org.claim_state is not ClaimState.CLAIM_PENDING:
            raise DomainRuleError("org has no pending claim")
        if edge.role is not MemberRole.ADMIN or not edge.is_current:
            raise DomainRuleError("edge is not a pending admin claim")
        verified = replace(
            edge.provenance,
            last_confirmed_at=now,
            verification=VerificationStatus.VERIFIED,
        )
        edge = self._store.save_edge(edge.confirmed(by=staff_id).with_provenance(verified))
        org_provenance = replace(org.provenance, last_confirmed_at=now)
        self._store.save_node(
            org.with_claim_state(ClaimState.CLAIMED).with_provenance(org_provenance)
        )
        return edge

    # --- internals -----------------------------------------------------------

    def _own_edge(self, edge_id: str, by_person_id: str) -> MemberOf:
        edge = self._store.edge(edge_id, MemberOf)
        if edge.source_id != by_person_id:
            raise PermissionDeniedError("only the member can do that")
        return edge

    def _release_org(self, org_id: str) -> None:
        org = self._store.node(org_id, Organization)
        self._store.save_node(org.with_claim_state(ClaimState.UNCLAIMED))

    def release_if_last_admin(self, edge: MemberOf) -> None:
        """Used by account erasure so an org is never left adminless."""
        if edge.is_admin and len(self.admins(edge.target_id)) == 1:
            self._release_org(edge.target_id)
