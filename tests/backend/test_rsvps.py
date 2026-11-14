"""RSVPs, who can see them, and "friends going" in Discover."""

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph, NotFoundError, PermissionDeniedError
from backend.graph.discovery import DiscoveryQuery
from backend.graph.rsvps import MIN_PUBLIC_COUNT
from backend.objects import DomainRuleError, Rsvp, RsvpStatus, Visibility
from backend.seed import seed_mock_data

from .conftest import NOW, make_event, make_person

GOING, INTERESTED = RsvpStatus.GOING, RsvpStatus.INTERESTED


def publish(world, title="Bonfire", days=3, **extra):
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def connect(world, a, b):
    request = world.graph.connections.request(a.id, b.id, NOW)
    world.graph.connections.accept(request.id, b.id)


def rsvp(world, person, event, status=GOING, at=NOW):
    return world.graph.rsvps.respond(event.id, person.id, status, at)


def names(people) -> list[str]:
    return [p.display_name for p in people]


# --- responding ----------------------------------------------------------------


def test_rsvp_records_status_and_can_be_changed_or_cleared(world):
    event = publish(world)
    first = rsvp(world, world.carol, event, INTERESTED)
    changed = rsvp(world, world.carol, event, GOING, NOW + timedelta(hours=1))
    assert changed.id == first.id
    assert changed.status is GOING
    assert changed.updated_at == NOW + timedelta(hours=1)
    assert world.graph.rsvps.mine(event.id, world.carol.id).status is GOING
    assert rsvp(world, world.carol, event, None) is None
    assert world.graph.rsvps.mine(event.id, world.carol.id) is None
    assert world.graph.store.edges(Rsvp, include_inactive=True) == []


def test_only_published_upcoming_events_take_rsvps(world):
    draft = world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    with pytest.raises(NotFoundError):  # drafts look like missing events
        rsvp(world, world.carol, draft)
    event = publish(world)
    world.graph.events.cancel(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        rsvp(world, world.carol, event)


def test_rsvps_freeze_when_the_event_ends(world):
    event = publish(world)
    rsvp(world, world.carol, event)
    after = event.ends_at + timedelta(minutes=1)
    with pytest.raises(DomainRuleError, match="ended"):
        rsvp(world, world.carol, event, INTERESTED, after)
    with pytest.raises(DomainRuleError, match="ended"):
        rsvp(world, world.carol, event, None, after)


def test_you_cannot_rsvp_to_an_event_you_cannot_see(world):
    event = publish(world, visibility=Visibility.MEMBERS)
    with pytest.raises(NotFoundError):
        rsvp(world, world.carol, event)
    assert rsvp(world, world.alice, event).status is GOING


def test_full_events_refuse_new_going_but_allow_interested(world):
    event = publish(world, capacity=1)
    rsvp(world, world.alice, event)
    with pytest.raises(DomainRuleError, match="full"):
        rsvp(world, world.carol, event)
    assert rsvp(world, world.carol, event, INTERESTED).status is INTERESTED
    assert rsvp(world, world.alice, event).status is GOING  # re-saving yours is fine


# --- who can see an RSVP -------------------------------------------------------


def test_friends_going_lists_only_connections_who_are_going(world):
    event = publish(world)
    dana = make_person(world.graph, "Dana")
    connect(world, world.carol, world.alice)
    connect(world, world.carol, world.bob)
    rsvp(world, world.alice, event)
    rsvp(world, world.bob, event, INTERESTED)
    rsvp(world, dana, event)  # going, but not Carol's friend
    assert names(world.graph.rsvps.friends_going(world.carol.id, event.id)) == ["Alice"]
    assert world.graph.rsvps.friends_going(None, event.id) == ()


def test_people_who_hide_their_rsvps_are_never_named(world):
    event = publish(world)
    connect(world, world.carol, world.alice)
    world.graph.rsvps.set_rsvp_visibility(world.alice.id, world.alice.id, Visibility.PRIVATE)
    rsvp(world, world.alice, event)
    assert world.graph.rsvps.friends_going(world.carol.id, event.id) == ()


def test_changing_the_setting_updates_existing_rsvps(world):
    event = publish(world)
    connect(world, world.carol, world.alice)
    rsvp(world, world.alice, event)
    world.graph.rsvps.set_rsvp_visibility(world.alice.id, world.alice.id, Visibility.PRIVATE)
    assert world.graph.rsvps.friends_going(world.carol.id, event.id) == ()
    world.graph.rsvps.set_rsvp_visibility(
        world.alice.id, world.alice.id, Visibility.CONNECTIONS
    )
    assert names(world.graph.rsvps.friends_going(world.carol.id, event.id)) == ["Alice"]


def test_only_you_choose_who_sees_your_rsvps_and_only_sensible_levels(world):
    with pytest.raises(PermissionDeniedError):
        world.graph.rsvps.set_rsvp_visibility(
            world.alice.id, world.bob.id, Visibility.PRIVATE
        )
    with pytest.raises(DomainRuleError):
        world.graph.rsvps.set_rsvp_visibility(
            world.alice.id, world.alice.id, Visibility.MEMBERS
        )


# --- counts --------------------------------------------------------------------


def test_small_counts_are_hidden_and_larger_ones_include_everyone(world):
    event = publish(world)
    world.graph.rsvps.set_rsvp_visibility(world.alice.id, world.alice.id, Visibility.PRIVATE)
    rsvp(world, world.alice, event)  # private people still count in the total
    for i in range(MIN_PUBLIC_COUNT - 2):
        rsvp(world, make_person(world.graph, f"Guest {i}"), event)
    assert world.graph.rsvps.going_count(event.id) is None
    rsvp(world, world.carol, event)
    assert world.graph.rsvps.going_count(event.id) == MIN_PUBLIC_COUNT


# --- discover ------------------------------------------------------------------


def discover(world, viewer):
    return world.graph.discovery.search(DiscoveryQuery(viewer_id=viewer.id), NOW)


def test_friends_going_lifts_an_event_and_explains_why(world):
    publish(world, "Sooner, no friends", days=1)
    later = publish(world, "Later, with friends", days=10)
    for friend in (world.alice, world.bob):
        connect(world, world.carol, friend)
        rsvp(world, friend, later)
    page = discover(world, world.carol)
    top = page.items[0]
    assert top.event.title == "Later, with friends"
    assert names(top.friends_going) == ["Alice", "Bob"]
    assert "2 friends going" in top.reasons
    assert page.items[1].friends_going == ()


def test_discover_shows_your_own_rsvp_and_the_count(world):
    event = publish(world)
    rsvp(world, world.carol, event, INTERESTED)
    item = discover(world, world.carol).items[0]
    assert item.my_rsvp is INTERESTED
    assert item.going_count is None
    logged_out = world.graph.discovery.search(DiscoveryQuery(), NOW).items[0]
    assert logged_out.my_rsvp is None
    assert logged_out.friends_going == ()


# --- account erasure and demo data ---------------------------------------------


def test_erasing_an_account_erases_its_rsvps(world):
    event = publish(world)
    rsvp(world, world.carol, event)
    world.graph.privacy.erase_person(world.carol.id)
    assert world.graph.store.edges(Rsvp, include_inactive=True) == []


def test_seeded_demo_has_rsvps_and_friends_going():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    assert len(graph.store.edges(Rsvp)) >= 60
    page = graph.discovery.search(DiscoveryQuery(viewer_id=result.people["drew"]), NOW)
    assert any(item.friends_going for item in page.items)
    assert graph.integrity.audit() == ()
