"""Every edge in the graph must be a genuine, consented relationship:
right kinds of endpoints, a recorded confirmer, and both people agreeing
to a connection. The integrity audit flags anything that isn't."""

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph, PermissionDeniedError
from backend.graph.store import InMemoryGraphStore
from backend.objects import (
    ClaimState,
    Connected,
    DomainRuleError,
    HostRole,
    Hosts,
    InstanceOf,
    MemberOf,
    MemberRole,
    Organization,
    OrgKind,
    PartOf,
    PartOfKind,
    Provenance,
    ProvenanceMethod,
    Tag,
    Tagged,
)
from backend.seed import seed_mock_data

from .conftest import NOW, STAFF, make_event, make_org, staff_provenance


def make_tag(graph) -> Tag:
    return graph.store.save_node(Tag(slug="robotics", label="Robotics",
                                     provenance=staff_provenance()))


def problems(graph) -> list[str]:
    return [issue.problem for issue in graph.integrity.audit()]


# --- edges only connect the right kinds of nodes -----------------------------


@pytest.mark.parametrize("build", [
    lambda w, tag: MemberOf(provenance=staff_provenance(), source_id=w.chapter.id, target_id=w.national.id),
    lambda w, tag: MemberOf(provenance=staff_provenance(), source_id=w.chapter.id, target_id=w.carol.id),
    lambda w, tag: Connected(provenance=staff_provenance(), source_id=w.carol.id, target_id=w.chapter.id),
    lambda w, tag: Hosts(provenance=staff_provenance(), source_id=w.carol.id, target_id=tag.id, role=HostRole.PRIMARY),
    lambda w, tag: PartOf(provenance=staff_provenance(), source_id=w.carol.id, target_id=w.chapter.id,
                          kind=PartOfKind.CHAPTER_OF),
    lambda w, tag: Tagged(provenance=staff_provenance(), source_id=w.carol.id, target_id=tag.id),
    lambda w, tag: InstanceOf(provenance=staff_provenance(), source_id=w.chapter.id, target_id=tag.id),
], ids=["org-member-of-org", "org-member-of-person", "person-connected-to-org",
        "person-hosts-tag", "person-part-of-org", "person-tagged", "org-instance-of-tag"])
def test_store_rejects_edges_between_the_wrong_kinds_of_nodes(world, build):
    tag = make_tag(world.graph)
    edge = build(world, tag)
    with pytest.raises(DomainRuleError):
        world.graph.store.save_edge(edge)


# --- memberships record who confirmed them -----------------------------------


def test_confirmation_records_the_confirming_admin(world):
    request = world.graph.membership.request(world.carol.id, world.chapter.id, NOW)
    confirmed = world.graph.membership.confirm(request.id, world.alice.id)
    assert confirmed.confirmed_by == world.alice.id


def test_claim_approval_records_the_staff_member(world):
    edge = world.graph.store.edge(world.alice_edge_id, MemberOf)
    assert edge.confirmed_by == STAFF


def test_a_confirmed_membership_must_say_who_confirmed_it(world):
    with pytest.raises(DomainRuleError):
        MemberOf(source_id=world.carol.id, target_id=world.chapter.id,
                 org_confirmed=True, provenance=staff_provenance())


def test_nobody_confirms_their_own_membership(world):
    with pytest.raises(DomainRuleError):
        MemberOf(source_id=world.carol.id, target_id=world.chapter.id,
                 org_confirmed=True, confirmed_by=world.carol.id,
                 provenance=staff_provenance())


def test_an_ended_membership_cannot_be_confirmed(world):
    request = world.graph.membership.request(world.carol.id, world.chapter.id, NOW)
    world.graph.membership.leave(request.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        world.graph.membership.confirm(request.id, world.alice.id)


# --- person <-> person connections need both people --------------------------


def test_a_connection_request_is_not_a_connection(world):
    world.graph.connections.request(world.alice.id, world.bob.id, NOW)
    assert not world.graph.connections.are_connected(world.alice.id, world.bob.id)
    assert world.graph.connections.connections_of(world.alice.id) == []


def test_only_the_recipient_can_accept(world):
    request = world.graph.connections.request(world.alice.id, world.bob.id, NOW)
    for outsider in (world.alice, world.carol):
        with pytest.raises(PermissionDeniedError):
            world.graph.connections.accept(request.id, outsider.id)
    world.graph.connections.accept(request.id, world.bob.id)
    assert world.graph.connections.are_connected(world.bob.id, world.alice.id)
    assert world.graph.connections.connections_of(world.alice.id) == [world.bob.id]
    assert world.graph.privacy.are_connected(world.alice.id, world.bob.id)


def test_duplicate_self_and_non_person_requests_are_rejected(world):
    connections = world.graph.connections
    connections.request(world.alice.id, world.bob.id, NOW)
    with pytest.raises(DomainRuleError):
        connections.request(world.bob.id, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        connections.request(world.carol.id, world.carol.id, NOW)
    with pytest.raises(LookupError):
        connections.request(world.carol.id, world.chapter.id, NOW)


@pytest.mark.parametrize("remover", ["alice", "bob"])
def test_either_person_can_remove_a_connection_without_a_trace(world, remover):
    connections = world.graph.connections
    request = connections.request(world.alice.id, world.bob.id, NOW)
    connections.accept(request.id, world.bob.id)
    connections.remove(request.id, getattr(world, remover).id)
    assert not connections.are_connected(world.alice.id, world.bob.id)
    assert world.graph.store.edges(Connected, include_inactive=True) == []


def test_recipient_can_decline_and_outsiders_cannot_remove(world):
    connections = world.graph.connections
    request = connections.request(world.alice.id, world.bob.id, NOW)
    with pytest.raises(PermissionDeniedError):
        connections.remove(request.id, world.carol.id)
    connections.remove(request.id, world.bob.id)
    assert world.graph.store.edges(Connected, include_inactive=True) == []


# --- integrity audit ---------------------------------------------------------


def test_graph_built_through_services_is_clean(world):
    world.graph.connections.accept(
        world.graph.connections.request(world.alice.id, world.carol.id, NOW).id,
        world.carol.id,
    )
    world.graph.events.create(make_event(), world.chapter.id, world.alice.id)
    assert problems(world.graph) == []


def test_seeded_mock_data_is_clean():
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)
    assert problems(graph) == []


def test_audit_flags_membership_confirmed_by_a_non_admin(world):
    world.graph.store.save_edge(
        MemberOf(source_id=world.carol.id, target_id=world.chapter.id,
                 role=MemberRole.ADMIN, org_confirmed=True, confirmed_by=world.bob.id,
                 provenance=staff_provenance())
    )
    assert any("not an admin" in p for p in problems(world.graph))


def test_audit_flags_admins_on_unclaimed_orgs_and_claimed_orgs_without_admins(world):
    club = make_org(world.graph, "Club", OrgKind.CLUB)
    world.graph.store.save_node(club.with_claim_state(ClaimState.CLAIMED))
    found = problems(world.graph)
    assert any("claimed but has no admin" in p for p in found)

    chapter = world.graph.store.node(world.chapter.id, Organization)
    world.graph.store.save_node(chapter.with_claim_state(ClaimState.UNCLAIMED))
    assert any("admin of an unclaimed org" in p for p in problems(world.graph))


def test_audit_flags_connections_neither_person_asked_for(world):
    world.graph.store.save_edge(
        Connected(source_id=world.alice.id, target_id=world.bob.id,
                  source_accepted=True, target_accepted=True,
                  provenance=Provenance(created_by=world.carol.id,
                                        method=ProvenanceMethod.SELF_CLAIM,
                                        created_at=NOW))
    )
    assert any("neither person requested" in p for p in problems(world.graph))


def test_audit_flags_published_events_without_a_primary_host(world):
    world.graph.store.save_node(make_event().published(NOW))
    assert any("primary host" in p for p in problems(world.graph))


def test_audit_flags_wrongly_typed_edges_written_before_the_check():
    store = InMemoryGraphStore()
    graph = CollectivizeGraph(store)
    org = make_org(graph, "Club", OrgKind.CLUB)
    other = make_org(graph, "Other", OrgKind.CLUB)
    legacy = MemberOf(source_id=org.id, target_id=other.id, provenance=staff_provenance())
    store._edges[legacy.id] = legacy  # simulate data that predates validation
    assert any("must start at a Person" in p for p in problems(graph))
