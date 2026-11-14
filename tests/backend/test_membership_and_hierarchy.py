import pytest

from backend.graph import PermissionDeniedError
from backend.graph import hierarchy as hierarchy_module
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


# --- membership edge cases ---------------------------------------------------


def test_duplicate_membership_request_is_rejected(world):
    m = world.graph.membership
    m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(DomainRuleError):
        m.request(world.carol.id, world.chapter.id, NOW)


def test_former_member_can_request_again_and_needs_reconfirmation(world):
    m = world.graph.membership
    first = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(first.id, world.alice.id)
    m.leave(first.id, world.carol.id, NOW)
    second = m.request(world.carol.id, world.chapter.id, NOW)
    assert second.id != first.id
    assert not m.is_member(world.carol.id, world.chapter.id)


def test_membership_cannot_be_left_twice_or_by_someone_else(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.leave(edge.id, world.alice.id, NOW)
    m.leave(edge.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        m.leave(edge.id, world.carol.id, NOW)


def test_leaving_keeps_visibility_unless_asked_to_hide(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.set_visibility(edge.id, world.carol.id, Visibility.PUBLIC)
    assert m.leave(edge.id, world.carol.id, NOW).visibility is Visibility.PUBLIC


def test_unconfirmed_member_cannot_be_given_a_role(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(DomainRuleError):
        m.set_role(edge.id, MemberRole.OFFICER, world.alice.id)


def test_admin_can_step_down_once_another_admin_exists(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_role(edge.id, MemberRole.ADMIN, world.alice.id)
    m.set_role(world.alice_edge_id, MemberRole.MEMBER, world.alice.id)
    assert not m.is_admin(world.alice.id, world.chapter.id)
    assert m.is_member(world.alice.id, world.chapter.id)


def test_pending_claim_grants_no_admin_rights(world):
    m = world.graph.membership
    edge = m.claim_org(world.carol.id, world.council.id, NOW)
    assert edge.role is MemberRole.ADMIN
    assert not m.is_admin(world.carol.id, world.council.id)
    assert org_state(world, world.council.id) is ClaimState.CLAIM_PENDING


def test_claimed_or_pending_orgs_cannot_be_claimed_again(world):
    m = world.graph.membership
    with pytest.raises(DomainRuleError):
        m.claim_org(world.carol.id, world.chapter.id, NOW)
    m.claim_org(world.carol.id, world.council.id, NOW)
    with pytest.raises(DomainRuleError):
        m.claim_org(world.bob.id, world.council.id, NOW)


def test_claim_cannot_be_approved_twice_or_for_a_plain_member(world):
    m = world.graph.membership
    with pytest.raises(DomainRuleError):
        m.approve_claim(world.alice_edge_id, STAFF, NOW)
    claim = m.claim_org(world.carol.id, world.council.id, NOW)
    bystander = m.request(world.bob.id, world.council.id, NOW)
    with pytest.raises(DomainRuleError):
        m.approve_claim(bystander.id, STAFF, NOW)
    m.approve_claim(claim.id, STAFF, NOW)


def test_existing_member_can_claim_an_unclaimed_org(world):
    m = world.graph.membership
    request = m.request(world.carol.id, world.council.id, NOW)
    claim = m.claim_org(world.carol.id, world.council.id, NOW)
    assert claim.id == request.id
    m.approve_claim(claim.id, STAFF, NOW)
    assert m.is_admin(world.carol.id, world.council.id)


# --- hierarchy edge cases ----------------------------------------------------


def test_pending_proposal_blocks_a_duplicate(world):
    h = world.graph.hierarchy
    h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.bob.id, NOW)


def test_proposer_cannot_accept_on_behalf_of_the_other_side(world):
    h = world.graph.hierarchy
    edge = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    assert not h.accept(edge.id, world.alice.id).is_established


def test_parent_side_can_propose_and_child_accepts(world):
    h = world.graph.hierarchy
    edge = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.bob.id, NOW)
    assert edge.parent_accepted and not edge.child_accepted
    h.accept(edge.id, world.alice.id)
    assert [o.id for o in h.children(world.national.id)] == [world.chapter.id]


def test_admin_of_both_orgs_establishes_link_immediately(world):
    m, h = world.graph.membership, world.graph.hierarchy
    edge = m.request(world.alice.id, world.national.id, NOW)
    m.confirm(edge.id, world.bob.id)
    m.set_role(edge.id, MemberRole.ADMIN, world.bob.id)
    link = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    assert link.is_established


def test_staff_can_verify_a_pending_proposal(world):
    h = world.graph.hierarchy
    edge = h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    verified = h.staff_verify(edge.id, NOW)
    assert verified.is_established
    assert verified.provenance.verification is VerificationStatus.VERIFIED


def test_pending_links_count_as_ancestors_only_when_asked(world):
    h = world.graph.hierarchy
    h.propose(world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW)
    assert h.ancestor_ids(world.chapter.id) == set()
    assert h.ancestor_ids(world.chapter.id, include_pending=True) == {world.national.id}


@pytest.mark.parametrize("child_kind, parent_kind, kind", [
    (OrgKind.CLUB, OrgKind.NATIONAL, PartOfKind.CHAPTER_OF),
    (OrgKind.NATIONAL, OrgKind.COUNCIL, PartOfKind.MEMBER_OF_COUNCIL),
    (OrgKind.INSTITUTION, OrgKind.INSTITUTION, PartOfKind.UNDER_INSTITUTION),
])
def test_disallowed_kind_combinations(world, child_kind, parent_kind, kind):
    child = make_org(world.graph, "Child", child_kind)
    parent = make_org(world.graph, "Parent", parent_kind)
    with pytest.raises(DomainRuleError):
        world.graph.hierarchy.staff_link(child.id, parent.id, kind, STAFF, NOW)


def test_cycles_are_rejected_even_through_pending_links(world, monkeypatch):
    # The kind table makes cycles unreachable today; loosen it so the
    # cycle guard itself is exercised.
    every_kind = frozenset(OrgKind)
    monkeypatch.setitem(
        hierarchy_module.ALLOWED_PART_OF, PartOfKind.UNDER_INSTITUTION, (every_kind, every_kind)
    )
    h = world.graph.hierarchy
    h.staff_link(world.chapter.id, world.national.id, PartOfKind.UNDER_INSTITUTION, STAFF, NOW)
    h.propose(world.national.id, world.council.id, PartOfKind.UNDER_INSTITUTION, world.bob.id, NOW)
    with pytest.raises(DomainRuleError):
        h.staff_link(world.council.id, world.chapter.id, PartOfKind.UNDER_INSTITUTION, STAFF, NOW)
