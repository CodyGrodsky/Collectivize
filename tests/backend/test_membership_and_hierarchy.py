import pytest

from backend.graph import PermissionDeniedError
from backend.objects import (
    ClaimState,
    DomainRuleError,
    MemberRole,
    Organization,
    OrgKind,
    PartOfKind,
    VerificationStatus,
    Visibility,
)

from .conftest import NOW, STAFF, make_org


def org_state(world, org_id: str) -> ClaimState:
    return world.graph.store.node(org_id, Organization).claim_state


# --- membership --------------------------------------------------------------


def test_claim_flow_marks_org_claimed_and_verified(world):
    m = world.graph.membership
    assert m.is_admin(world.alice.id, world.chapter.id)
    assert org_state(world, world.chapter.id) is ClaimState.CLAIMED
    edge = m.current(world.alice.id, world.chapter.id)
    assert edge.provenance.verification is VerificationStatus.VERIFIED


def test_requested_membership_needs_org_confirmation(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    assert not m.is_member(world.carol.id, world.chapter.id)
    m.confirm(edge.id, world.alice.id)
    assert m.is_member(world.carol.id, world.chapter.id)


def test_non_admin_cannot_confirm_or_grant_roles(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.confirm(edge.id, world.bob.id)
    with pytest.raises(PermissionDeniedError):
        m.set_role(edge.id, MemberRole.ADMIN, world.carol.id)


def test_only_person_controls_membership_visibility(world):
    m = world.graph.membership
    with pytest.raises(PermissionDeniedError):
        m.set_visibility(world.alice_edge_id, world.bob.id, Visibility.PUBLIC)
    updated = m.set_visibility(world.alice_edge_id, world.alice.id, Visibility.PUBLIC)
    assert updated.visibility is Visibility.PUBLIC


def test_last_admin_cannot_leave_without_handoff(world):
    m = world.graph.membership
    with pytest.raises(DomainRuleError):
        m.leave(world.alice_edge_id, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        m.set_role(world.alice_edge_id, MemberRole.MEMBER, world.alice.id)


def test_last_admin_can_release_org_to_unclaimed(world):
    m = world.graph.membership
    ended = m.leave(
        world.alice_edge_id, world.alice.id, NOW, keep_visible=False, release_org=True
    )
    assert ended.ended_at == NOW and ended.visibility is Visibility.PRIVATE
    assert org_state(world, world.chapter.id) is ClaimState.UNCLAIMED


def test_admin_can_leave_after_handoff(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_role(edge.id, MemberRole.ADMIN, world.alice.id)
    m.leave(world.alice_edge_id, world.alice.id, NOW)
    assert m.is_admin(world.carol.id, world.chapter.id)
    assert not m.is_admin(world.alice.id, world.chapter.id)


# --- hierarchy ---------------------------------------------------------------


def test_part_of_requires_both_sides(world):
    h = world.graph.hierarchy
    edge = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    assert not edge.is_established
    assert h.parents(world.chapter.id) == []
    h.accept(edge.id, world.bob.id)
    assert [o.id for o in h.parents(world.chapter.id)] == [world.national.id]


def test_outsider_cannot_propose_or_accept(world):
    h = world.graph.hierarchy
    with pytest.raises(PermissionDeniedError):
        h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.carol.id, NOW)
    edge = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    with pytest.raises(PermissionDeniedError):
        h.accept(edge.id, world.carol.id)


def test_staff_link_is_verified_and_supports_multiple_parents(world):
    h = world.graph.hierarchy
    h.staff_link(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, STAFF, NOW)
    h.staff_link(world.chapter.id, world.council.id, PartOfKind.MEMBER_OF_COUNCIL, STAFF, NOW)
    assert {o.id for o in h.parents(world.chapter.id)} == {world.national.id, world.council.id}


def test_disallowed_kind_is_rejected(world):
    with pytest.raises(DomainRuleError):
        world.graph.hierarchy.staff_link(
            world.council.id, world.chapter.id, PartOfKind.CHAPTER_OF, STAFF, NOW
        )


def test_ancestors_are_transitive_and_duplicates_rejected(world):
    h = world.graph.hierarchy
    uni = make_org(world.graph, "University", OrgKind.INSTITUTION)
    h.staff_link(world.chapter.id, world.council.id, PartOfKind.MEMBER_OF_COUNCIL, STAFF, NOW)
    h.staff_link(world.council.id, uni.id, PartOfKind.UNDER_INSTITUTION, STAFF, NOW)
    assert h.ancestor_ids(world.chapter.id) == {world.council.id, uni.id}
    with pytest.raises(DomainRuleError):
        h.staff_link(world.council.id, uni.id, PartOfKind.UNDER_INSTITUTION, STAFF, NOW)
