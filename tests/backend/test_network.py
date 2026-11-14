"""The network view: who you're linked to, how you reach anyone, and what
you have in common. Only links the viewer is allowed to see are used."""

from itertools import pairwise

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph, NotFoundError, PermissionDeniedError
from backend.graph.network import LinkKind, NodeKind
from backend.graph.roster import RosterPolicy
from backend.objects import DomainRuleError, Organization, OrgKind, Visibility
from backend.seed import seed_mock_data

from .conftest import NOW, make_admin, make_org, make_person


def connect(world, a, b):
    request = world.graph.connections.request(a.id, b.id, NOW)
    world.graph.connections.accept(request.id, b.id)


def join(world, person, org, admin):
    edge = world.graph.membership.request(person.id, org.id, NOW)
    world.graph.membership.confirm(edge.id, admin.id)
    return edge


def path_names(world, path) -> list[str]:
    return [node.label for node in path.nodes]


def neighbor_ids(view, center_id) -> set[str]:
    return {
        link.target if link.source == center_id else link.source
        for link in view.links
        if center_id in (link.source, link.target)
    }


# --- neighborhood --------------------------------------------------------------


def test_neighborhood_shows_accepted_connections_and_memberships_only(world):
    connect(world, world.alice, world.bob)
    world.graph.connections.request(world.alice.id, world.carol.id, NOW)  # pending
    world.graph.membership.request(world.alice.id, world.national.id, NOW)  # pending
    view = world.graph.network.neighborhood(world.alice.id)
    assert neighbor_ids(view, world.alice.id) == {world.bob.id, world.chapter.id}
    kinds = {link.kind for link in view.links}
    assert kinds == {LinkKind.CONNECTION, LinkKind.MEMBERSHIP}
    nodes = {node.id: node for node in view.nodes}
    assert nodes[world.chapter.id].kind is NodeKind.ORG
    assert nodes[world.bob.id].kind is NodeKind.PERSON
    assert nodes[world.alice.id].label == "Alice"


def test_neighborhood_can_go_deeper_and_is_capped(world):
    connect(world, world.alice, world.bob)
    connect(world, world.bob, world.carol)
    one = world.graph.network.neighborhood(world.alice.id)
    two = world.graph.network.neighborhood(world.alice.id, depth=2)
    assert world.carol.id not in {n.id for n in one.nodes}
    assert world.carol.id in {n.id for n in two.nodes}
    capped = world.graph.network.neighborhood(world.alice.id, depth=2, max_nodes=2)
    assert len(capped.nodes) == 2
    assert capped.truncated
    assert all({l.source, l.target} <= {n.id for n in capped.nodes} for l in capped.links)


def test_logged_out_visitors_have_no_network(world):
    with pytest.raises(PermissionDeniedError):
        world.graph.network.neighborhood(None)


def test_hidden_orgs_do_not_appear(world):
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    view = world.graph.network.neighborhood(world.carol.id, world.alice.id)
    assert world.chapter.id not in {n.id for n in view.nodes}


# --- privacy -------------------------------------------------------------------


def test_hide_connections_hides_your_links_from_everyone_else(world):
    connect(world, world.alice, world.bob)
    world.graph.connections.set_hide_connections(world.bob.id, world.bob.id, True)
    as_carol = world.graph.network.neighborhood(world.carol.id, world.bob.id)
    assert neighbor_ids(as_carol, world.bob.id) == {world.national.id}  # orgs still show
    as_alice = world.graph.network.neighborhood(world.alice.id)
    assert world.bob.id in neighbor_ids(as_alice, world.alice.id)  # you see your own


def test_only_you_can_hide_your_connections(world):
    with pytest.raises(PermissionDeniedError):
        world.graph.connections.set_hide_connections(world.bob.id, world.alice.id, True)


def test_private_memberships_are_not_links_to_others(world):
    edge = join(world, world.carol, world.chapter, world.alice)
    world.graph.membership.set_visibility(edge.id, world.carol.id, Visibility.PRIVATE)
    as_bob = world.graph.network.neighborhood(world.bob.id, world.chapter.id)
    assert world.carol.id not in neighbor_ids(as_bob, world.chapter.id)
    as_carol = world.graph.network.neighborhood(world.carol.id)
    assert world.chapter.id in neighbor_ids(as_carol, world.carol.id)


def test_member_links_follow_the_member_list_policy(world):
    graph = CollectivizeGraph(
        world.graph.store, roster_policy=RosterPolicy(audience=Visibility.MEMBERS)
    )
    view = graph.network.neighborhood(world.carol.id, world.chapter.id)
    assert neighbor_ids(view, world.chapter.id) == set()
    own = graph.network.neighborhood(world.alice.id)
    assert world.chapter.id in neighbor_ids(own, world.alice.id)


# --- paths ---------------------------------------------------------------------


def test_path_through_a_friend(world):
    connect(world, world.carol, world.alice)
    connect(world, world.alice, world.bob)
    path = world.graph.network.find_path(world.carol.id, world.bob.id)
    assert path_names(world, path) == ["Carol", "Alice", "Bob"]
    assert path.hops == 2


def test_path_through_a_shared_org(world):
    join(world, world.carol, world.chapter, world.alice)
    path = world.graph.network.find_path(world.carol.id, world.alice.id)
    assert path_names(world, path) == ["Carol", "Alpha Chapter", "Alice"]


def test_people_are_preferred_over_orgs_when_paths_are_equally_short(world):
    dana = make_person(world.graph, "Dana")
    join(world, world.carol, world.chapter, world.alice)
    join(world, dana, world.chapter, world.alice)
    connect(world, world.carol, world.bob)
    connect(world, world.bob, dana)
    path = world.graph.network.find_path(world.carol.id, dana.id)
    assert path_names(world, path) == ["Carol", "Bob", "Dana"]


def test_paths_longer_than_four_hops_are_not_returned(world):
    chain = [world.carol] + [make_person(world.graph, f"P{i}") for i in range(5)]
    for a, b in pairwise(chain):
        connect(world, a, b)
    assert world.graph.network.find_path(chain[0].id, chain[4].id).hops == 4
    assert world.graph.network.find_path(chain[0].id, chain[5].id) is None


def test_hidden_people_never_route_a_path(world):
    connect(world, world.carol, world.alice)
    connect(world, world.alice, world.bob)
    world.graph.connections.set_hide_connections(world.alice.id, world.alice.id, True)
    assert world.graph.network.find_path(world.carol.id, world.bob.id) is None


def test_path_to_yourself_and_to_orgs(world):
    assert world.graph.network.find_path(world.carol.id, world.carol.id).hops == 0
    join(world, world.carol, world.chapter, world.alice)
    path = world.graph.network.find_path(world.carol.id, world.chapter.id)
    assert path.hops == 1


def test_unknown_or_invisible_targets_look_the_same(world):
    with pytest.raises(NotFoundError):
        world.graph.network.find_path(world.carol.id, "missing")
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    with pytest.raises(NotFoundError):
        world.graph.network.find_path(world.carol.id, world.chapter.id)


# --- focus ---------------------------------------------------------------------


def test_focus_shows_the_node_its_neighbors_and_the_path_from_you(world):
    connect(world, world.carol, world.alice)
    connect(world, world.alice, world.bob)
    view = world.graph.network.focus(world.carol.id, world.bob.id)
    ids = {n.id for n in view.nodes}
    assert {world.carol.id, world.alice.id, world.bob.id} <= ids
    assert world.national.id in ids  # Bob's org
    assert [n.id for n in view.path.nodes] == [world.carol.id, world.alice.id, world.bob.id]


def test_focus_without_a_close_path_still_shows_the_node(world):
    view = world.graph.network.focus(world.carol.id, world.bob.id)
    assert view.path is None
    assert world.bob.id in {n.id for n in view.nodes}


# --- in common -----------------------------------------------------------------


def test_in_common_lists_mutual_friends_and_shared_orgs(world):
    dana = make_person(world.graph, "Dana")
    for person in (world.carol, dana):
        connect(world, person, world.bob)
        join(world, person, world.chapter, world.alice)
    common = world.graph.network.in_common(world.carol.id, dana.id)
    assert [p.display_name for p in common.people] == ["Bob"]
    assert [o.name for o in common.orgs] == ["Alpha Chapter"]
    assert common.summary == "1 mutual friend · Alpha Chapter"


def test_hidden_mutual_friends_are_left_out(world):
    dana = make_person(world.graph, "Dana")
    connect(world, world.carol, world.bob)
    connect(world, dana, world.bob)
    world.graph.connections.set_hide_connections(world.bob.id, world.bob.id, True)
    assert world.graph.network.in_common(world.carol.id, dana.id).people == ()


def test_in_common_with_yourself_is_rejected(world):
    with pytest.raises(DomainRuleError):
        world.graph.network.in_common(world.carol.id, world.carol.id)


# --- seeded demo network -------------------------------------------------------


def test_seeded_network_is_dense_and_connected():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    people = result.people
    assert len(people) >= 40
    assert len(result.orgs) >= 10
    degrees = [len(graph.connections.connections_of(p)) for p in people.values()]
    assert sum(d >= 3 for d in degrees) >= 35
    drew = people["drew"]
    reachable = sum(
        graph.network.find_path(drew, other) is not None
        for key, other in people.items()
        if key != "drew"
    )
    assert reachable >= len(people) * 0.8
    assert graph.integrity.audit() == ()


def test_unclaimed_org_has_no_member_links(world):
    club = make_org(world.graph, "Ghost Club", OrgKind.CLUB)
    view = world.graph.network.neighborhood(world.carol.id, club.id)
    assert neighbor_ids(view, club.id) == set()
    make_admin(world.graph, world.carol, club)
    view = world.graph.network.neighborhood(world.bob.id, club.id)
    assert neighbor_ids(view, club.id) == {world.carol.id}
