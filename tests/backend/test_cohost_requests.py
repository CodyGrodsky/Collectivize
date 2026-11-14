"""A person or org can ask to co-host someone else's event; one of the
event's current hosts must approve before it counts."""

from dataclasses import replace

import pytest

from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import DomainRuleError, HostOrigin, Visibility

from .conftest import NOW, make_event, make_person


def chapter_event(world, **extra):
    event = replace(make_event("Philanthropy 5K"), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def calendar_ids(world, host) -> set[str]:
    listing = world.graph.calendar.host_events(host.id, world.alice.id)
    return {e.id for e in listing.public + listing.private}


# --- asking --------------------------------------------------------------------


def test_org_admin_can_ask_to_co_host_and_it_stays_pending(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.national.id, world.bob.id, NOW)
    assert request.origin is HostOrigin.REQUESTED
    assert not request.accepted
    assert world.graph.events.host_ids(event.id) == {world.chapter.id}
    assert event.id not in calendar_ids(world, world.national)


def test_a_person_can_ask_for_themselves(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    assert request.source_id == world.carol.id


def test_nobody_can_ask_on_someone_elses_behalf(world):
    event = chapter_event(world)
    with pytest.raises(PermissionDeniedError):
        world.graph.co_hosting.request(event.id, world.national.id, world.carol.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.co_hosting.request(event.id, world.carol.id, world.bob.id, NOW)


def test_cannot_ask_to_join_an_event_you_cannot_see(world):
    hidden = chapter_event(world, visibility=Visibility.MEMBERS)
    draft = world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    for event_id in (hidden.id, draft.id, "missing"):
        with pytest.raises(NotFoundError):
            world.graph.co_hosting.request(event_id, world.carol.id, world.carol.id, NOW)


def test_cannot_ask_to_join_a_cancelled_event(world):
    event = chapter_event(world)
    world.graph.events.cancel(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)


def test_existing_hosts_and_duplicate_requests_are_rejected(world):
    event = chapter_event(world)
    co = world.graph.co_hosting
    with pytest.raises(DomainRuleError):
        co.request(event.id, world.chapter.id, world.alice.id, NOW)
    co.request(event.id, world.carol.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        co.request(event.id, world.carol.id, world.carol.id, NOW)
    world.graph.events.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        co.request(event.id, world.national.id, world.bob.id, NOW)


# --- answering -----------------------------------------------------------------


def test_only_a_current_host_can_approve_a_request(world):
    ev = world.graph.events
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.national.id, world.bob.id, NOW)
    for not_a_host in (world.bob, world.carol):
        with pytest.raises(PermissionDeniedError):
            ev.accept_co_host(request.id, not_a_host.id)
    ev.accept_co_host(request.id, world.alice.id)
    assert ev.host_ids(event.id) == {world.chapter.id, world.national.id}
    assert event.id in calendar_ids(world, world.national)


def test_an_accepted_co_host_can_approve_later_requests(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.carol.id)
    dana = make_person(world.graph, "Dana")
    request = world.graph.co_hosting.request(event.id, dana.id, dana.id, NOW)
    ev.accept_co_host(request.id, world.carol.id)
    assert dana.id in ev.host_ids(event.id)


def test_hosts_can_decline_and_requesters_can_withdraw(world):
    ev = world.graph.events
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    with pytest.raises(PermissionDeniedError):
        ev.remove_co_host(request.id, world.bob.id)
    ev.remove_co_host(request.id, world.alice.id)  # declined
    again = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    ev.remove_co_host(again.id, world.carol.id)  # withdrawn
    assert ev.host_ids(event.id) == {world.chapter.id}


def test_invitations_still_need_the_invited_party(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    assert invite.origin is HostOrigin.INVITED
    with pytest.raises(PermissionDeniedError):
        ev.accept_co_host(invite.id, world.alice.id)


# --- seeing what's waiting -----------------------------------------------------


def test_hosts_see_pending_invites_and_requests_and_outsiders_do_not(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    pending = world.graph.co_hosting.pending(event.id, world.alice.id)
    assert {e.id for e in pending} == {invite.id, request.id}
    with pytest.raises(PermissionDeniedError):
        world.graph.co_hosting.pending(event.id, world.carol.id)


def test_a_party_sees_what_it_has_been_invited_to_and_has_asked_for(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    assert [e.id for e in world.graph.co_hosting.pending_for(world.national.id, world.bob.id)] == [
        invite.id
    ]
    assert [e.id for e in world.graph.co_hosting.pending_for(world.carol.id, world.carol.id)] == [
        request.id
    ]
    with pytest.raises(PermissionDeniedError):
        world.graph.co_hosting.pending_for(world.national.id, world.carol.id)
