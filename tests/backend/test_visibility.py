"""The v1.1 visibility rules (spec 4.1-4.3), checked on the seeded demo."""

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph, InMemoryGraphStore
from backend.graph.visibility import NETWORK_PEOPLE_CAP, Card, ConnectionState
from backend.objects import Person
from backend.seed import seed_mock_data
from backend.seed_profiles import HUB_CONNECTIONS, email_for

from .conftest import NOW


@pytest.fixture(scope="module")
def demo():
    graph = CollectivizeGraph(InMemoryGraphStore())
    result = seed_mock_data(graph, Environment.TEST, NOW)
    return graph, result.people, result.orgs


def test_degrees_along_the_alice_bob_carol_chain(demo):
    graph, people, _ = demo
    v = graph.visibility
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    assert v.degree(alice, alice) == 0
    assert v.degree(alice, bob) == 1
    assert v.degree(alice, carol) == 2
    assert v.degree(carol, people["drew"]) == 3  # Carol-Bob-Alice-Drew


def test_alice_sees_bob_but_not_carol_in_her_network(demo):
    graph, people, _ = demo
    view = graph.visibility.visible_network(people["alice"], people["alice"])
    shown = {n.id for n in view.people}
    assert people["bob"] in shown
    assert people["carol"] not in shown


def test_a_connections_network_shows_their_connections_and_orgs(demo):
    graph, people, orgs = demo
    view = graph.visibility.visible_network(people["alice"], people["bob"])
    shown = {n.id: n for n in view.people}
    assert people["carol"] in shown
    assert shown[people["alice"]].is_viewer
    assert not shown[people["carol"]].viewer_connected
    assert shown[people["carol"]].connection is ConnectionState.NONE
    assert orgs["surf"] in {n.id for n in view.orgs}


def test_one_hop_only_no_network_for_second_degree(demo):
    graph, people, _ = demo
    v = graph.visibility
    assert v.visible_network(people["alice"], people["carol"]) is None
    sections = v.visible_profile_sections(people["alice"], people["carol"])
    assert sections.card is Card.PARTIAL
    assert not (sections.orgs or sections.events or sections.network or sections.pictures)


def test_connections_see_every_profile_section(demo):
    graph, people, _ = demo
    for viewer in (people["alice"], people["bob"]):  # self, and a connection
        sections = graph.visibility.visible_profile_sections(viewer, people["bob"])
        assert sections.card is Card.FULL
        assert sections.orgs and sections.events and sections.network


def test_preview_card_is_full_card_only(demo):
    graph, people, _ = demo
    sections = graph.visibility.visible_profile_sections(
        people["carol"], people["alice"], preview=True
    )
    assert sections.card is Card.FULL
    assert not (sections.orgs or sections.events or sections.network)


def test_connecting_makes_carol_first_degree():
    graph = CollectivizeGraph(InMemoryGraphStore())
    people = seed_mock_data(graph, Environment.TEST, NOW).people
    alice, carol = people["alice"], people["carol"]
    request = graph.connections.request(alice, carol, NOW)
    assert graph.visibility.connection_state(alice, carol) is ConnectionState.PENDING_OUT
    assert graph.visibility.connection_state(carol, alice) is ConnectionState.PENDING_IN
    graph.connections.accept(request.id, carol)
    assert carol in {n.id for n in graph.visibility.visible_network(alice, alice).people}
    assert graph.visibility.degree(alice, carol) == 1


def test_big_networks_are_capped_with_show_more(demo):
    graph, people, _ = demo
    rowan = people["rowan"]
    view = graph.visibility.visible_network(rowan, rowan)
    assert view.people_total >= HUB_CONNECTIONS > 100
    assert len(view.people) == NETWORK_PEOPLE_CAP
    assert view.has_more
    more = graph.visibility.visible_network(rowan, rowan, people_limit=2 * NETWORK_PEOPLE_CAP)
    assert len(more.people) == 2 * NETWORK_PEOPLE_CAP
    assert [n.id for n in more.people[:NETWORK_PEOPLE_CAP]] == [n.id for n in view.people]


def test_membership_shows_even_when_set_private(demo):
    graph, people, orgs = demo
    from backend.objects import MemberOf, Visibility

    edge = graph.store.edges(MemberOf, source_id=people["bob"], target_id=orgs["surf"])[0]
    graph.membership.set_visibility(edge.id, people["bob"], Visibility.PRIVATE)
    view = graph.visibility.visible_network(people["alice"], people["bob"])
    assert orgs["surf"] in {n.id for n in view.orgs}


def test_org_members_see_full_list_others_see_count_and_connections(demo):
    graph, people, orgs = demo
    v = graph.visibility
    surf = orgs["surf"]
    as_member = v.visible_members(people["bob"], surf)  # Bob is in Surf Club
    assert as_member.is_full and people["bob"] in as_member.member_ids
    as_friend = v.visible_members(people["alice"], surf)  # Alice isn't, but knows Bob
    assert as_friend.count == as_member.count
    assert people["bob"] in as_friend.member_ids
    mine = set(graph.connections.connections_of(people["alice"]))
    assert set(as_friend.member_ids) <= mine


def test_org_admin_and_platform_admin_flags(demo):
    graph, people, orgs = demo
    v = graph.visibility
    assert v.can_manage_org(people["casey"], orgs["robotics"])
    assert not v.can_manage_org(people["casey"], orgs["surf"])
    assert v.is_platform_admin(people["pat"])
    assert not v.is_platform_admin(people["casey"])


def test_demo_people_have_fictional_university_emails(demo):
    graph, people, _ = demo
    alice = graph.store.node(people["alice"], Person)
    assert alice.email == email_for("alice") == "alice.demo@northeastern.edu"


def test_profile_fields_are_validated():
    from backend.objects import DomainRuleError, ProfileTheme, Top3

    with pytest.raises(DomainRuleError):
        Top3(title="", items=("a", "b", "c"))
    with pytest.raises(DomainRuleError):
        Top3(title="spots", items=("a", "b"))
    with pytest.raises(DomainRuleError):
        Top3(title="spots", items=("a", "b", "x" * 41))
    with pytest.raises(DomainRuleError):
        ProfileTheme(outline="#123456")
    with pytest.raises(DomainRuleError):
        ProfileTheme(background="neon")
