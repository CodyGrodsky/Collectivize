"""The network map says which people shown belong to which orgs shown, so
the page can draw a line from each connection to the orgs you share."""

from backend.objects import Visibility

from .test_person_map import connect, friends_of_alice, join


def test_a_connection_in_an_org_you_share_is_tied_to_it(world):
    (dave,) = friends_of_alice(world, "Dave")
    join(world, dave, world.chapter, world.alice)
    view = world.graph.network.person_map(world.alice.id)
    assert view.memberships == ((dave.id, world.chapter.id),)


def test_a_connection_outside_your_orgs_has_no_tie(world):
    (dave,) = friends_of_alice(world, "Dave")
    join(world, dave, world.national, world.bob)  # an org Alice is not in
    view = world.graph.network.person_map(world.alice.id)
    assert world.national.id not in {o.id for o in view.orgs}
    assert view.memberships == ()


def test_a_private_membership_is_not_drawn(world):
    (dave,) = friends_of_alice(world, "Dave")
    edge = join(world, dave, world.chapter, world.alice)
    world.graph.membership.set_visibility(edge.id, dave.id, Visibility.PRIVATE)
    viewer = friends_of_alice(world, "Erin")[0]
    connect(world, viewer, dave)
    join(world, viewer, world.chapter, world.alice)
    view = world.graph.network.person_map(viewer.id)
    assert (dave.id, world.chapter.id) not in view.memberships


def test_on_a_connections_map_mutual_friends_are_tied_to_shared_orgs(world):
    dave, erin = friends_of_alice(world, "Dave", "Erin")
    connect(world, dave, erin)
    join(world, dave, world.chapter, world.alice)
    join(world, erin, world.chapter, world.alice)
    view = world.graph.network.person_map(dave.id, world.alice.id)  # Dave looks at Alice
    assert set(view.memberships) == {(dave.id, world.chapter.id), (erin.id, world.chapter.id)}


def test_an_orgs_own_map_has_no_extra_membership_ties(world):
    (dave,) = friends_of_alice(world, "Dave")
    join(world, dave, world.chapter, world.alice)
    assert world.graph.network.org_map(world.alice.id, world.chapter.id).memberships == ()
