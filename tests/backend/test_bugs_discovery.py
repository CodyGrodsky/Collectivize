"""Regression tests for bugs found in discovery, ranking, preferences,
activity, network and geo. Each test asserts the intended behaviour and is
marked xfail(strict=True) until the bug is fixed."""

from collections import Counter
from dataclasses import replace

import pytest

from backend.graph.discovery import DiscoveryQuery
from backend.objects import (
    DomainRuleError,
    MemberOf,
    Organization,
    RecommendationUse,
    Tag,
    Visibility,
)

from .conftest import NOW, make_event, staff_provenance


def connect(world, a, b):
    request = world.graph.connections.request(a.id, b.id, NOW)
    world.graph.connections.accept(request.id, b.id)


def join(world, person, org, admin):
    edge = world.graph.membership.request(person.id, org.id, NOW)
    return world.graph.membership.confirm(edge.id, admin.id)


def undirected(links):
    return Counter(frozenset((l.source, l.target)) for l in links)


# --- network -------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: connection links are emitted once per direction")
def test_neighborhood_draws_each_connection_once(world):
    connect(world, world.alice, world.bob)
    view = world.graph.network.neighborhood(world.alice.id)
    counts = undirected(view.links)
    assert counts[frozenset((world.alice.id, world.bob.id))] == 1


@pytest.mark.xfail(strict=True, reason="BUG: focus view duplicates connection links")
def test_focus_draws_each_connection_once(world):
    connect(world, world.alice, world.bob)
    connect(world, world.bob, world.carol)
    view = world.graph.network.focus(world.alice.id, world.carol.id)
    counts = undirected(view.links)
    assert all(n == 1 for n in counts.values()), counts


@pytest.mark.xfail(strict=True, reason="BUG: negative max_hops is unbounded")
def test_find_path_never_exceeds_max_hops(world):
    connect(world, world.alice, world.bob)
    path = world.graph.network.find_path(world.alice.id, world.bob.id, max_hops=-1)
    assert path is None or path.hops <= 0


# --- preferences consent -------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: org tags ignore membership recommendation_use=NONE")
def test_membership_marked_none_is_not_learned_from(world):
    robotics = world.graph.store.save_node(
        Tag(slug="robotics", label="Robotics", provenance=staff_provenance())
    )
    world.graph.events.tag(world.national.id, robotics.id, world.bob.id, NOW)
    edge = join(world, world.carol, world.national, world.bob)
    stored = world.graph.store.edge(edge.id, MemberOf)
    world.graph.store.save_edge(stored.with_recommendation_use(RecommendationUse.NONE))
    # activity.py honours the setting ...
    assert world.graph.activity.top_orgs(world.carol.id, NOW) == []
    # ... so the taste profile should too.
    profile = world.graph.preferences.profile(world.carol.id, NOW)
    assert robotics.id not in profile.org_tags


@pytest.mark.xfail(strict=True, reason="BUG: my_org_host ignores membership recommendation_use=NONE")
def test_membership_marked_none_gives_no_my_org_boost(world):
    event = publish(world, "Chapter social")
    edge = join(world, world.carol, world.chapter, world.alice)
    stored = world.graph.store.edge(edge.id, MemberOf)
    world.graph.store.save_edge(stored.with_recommendation_use(RecommendationUse.NONE))
    page = world.graph.discovery.search(DiscoveryQuery(viewer_id=world.carol.id), NOW)
    item = next(i for i in page.items if i.event.id == event.id)
    effects = {c.name: c.effect for c in item.factors}
    assert effects["my_org_host"] == 0.0


# --- discovery -----------------------------------------------------------------


def publish(world, title, org=None, admin=None, days=3, **extra):
    org = org or world.chapter
    admin = admin or world.alice
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, org.id, admin.id)
    return world.graph.events.publish(event.id, admin.id, NOW)


@pytest.mark.xfail(strict=True, reason="BUG: org_id filter probes hidden co-host orgs")
def test_org_filter_does_not_reveal_a_hidden_co_host(world):
    """Carol can't see the members-only national org, so filtering by it must
    not tell her which events it co-hosts."""
    event = publish(world, "Joint social")
    invite = world.graph.events.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    world.graph.events.accept_co_host(invite.id, world.bob.id)
    hidden = world.graph.store.node(world.national.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    assert not world.graph.privacy.can_view(world.carol.id, hidden)
    # the event itself is fine for Carol to see ...
    everything = world.graph.discovery.search(DiscoveryQuery(viewer_id=world.carol.id), NOW)
    assert [i.event.id for i in everything.items] == [event.id]
    # ... but filtering by the hidden org must behave as if it had no events
    page = world.graph.discovery.search(
        DiscoveryQuery(viewer_id=world.carol.id, org_id=world.national.id), NOW
    )
    assert page.items == ()


@pytest.mark.xfail(strict=True, reason="BUG: non-integer limit passes validation, crashes at slice")
def test_fractional_limit_is_rejected_up_front():
    with pytest.raises(DomainRuleError):
        DiscoveryQuery(limit=2.5)
