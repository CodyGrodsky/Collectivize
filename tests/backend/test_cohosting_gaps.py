"""Gaps found reviewing co-hosting, calendars, member lists and erasure.

Each test states the behavior we want. Ones that fail today are marked
`xfail(strict=True)`: BUG for clear defects, DESIGN for behavior that
still needs a product decision. A marker trips as soon as the code
changes to match, so it can be removed."""

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import DomainRuleError, HostRole, OrgKind, RecordState, Visibility

from .conftest import NOW, make_admin, make_event, make_org, make_person


def chapter_event(world, **extra):
    event = replace(make_event("Philanthropy 5K"), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def with_national_co_host(world):
    """A published chapter event that Bob's national org co-hosts."""
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    return event


def club_with_admin(world):
    club = make_org(world.graph, "Robotics Club", OrgKind.CLUB)
    dave = make_person(world.graph, "Dave")
    make_admin(world.graph, dave, club)
    return club, dave


# --- 1. member lists vs. membership visibility --------------------------------


@pytest.mark.parametrize("viewer", [None, "bob"])
def test_connections_only_membership_stays_off_the_member_list(world, viewer):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_visibility(edge.id, world.carol.id, Visibility.CONNECTIONS)
    viewer_id = getattr(world, viewer).id if viewer else None
    names = [e.person.display_name for e in world.graph.roster.members(world.chapter.id, viewer_id)]
    assert names == ["Alice"]


# --- 2. invitations and event status ------------------------------------------


def test_invitation_cannot_be_accepted_once_the_event_is_cancelled(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.cancel(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        ev.accept_co_host(invite.id, world.bob.id)


def test_cannot_invite_a_co_host_after_the_event_has_ended(world):
    event = chapter_event(world)
    after = event.ends_at + timedelta(days=1)
    with pytest.raises(DomainRuleError):
        world.graph.events.invite_co_host(event.id, world.national.id, world.alice.id, after)


def test_cannot_invite_a_co_host_to_an_archived_event(world):
    ev = world.graph.events
    event = chapter_event(world)
    ev.archive(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)


# --- 3. withdrawing an invitation ---------------------------------------------


def test_a_co_host_can_withdraw_an_invitation_it_sent(world):
    ev = world.graph.events
    event = with_national_co_host(world)
    club, _ = club_with_admin(world)
    invite = ev.invite_co_host(event.id, club.id, world.bob.id, NOW)
    assert ev.remove_co_host(invite.id, world.bob.id).state is RecordState.REMOVED


# --- 4. who takes over when the primary host is erased ------------------------


def test_erased_primary_passes_to_the_co_host_that_accepted_first(world):
    ev = world.graph.events
    club, dave = club_with_admin(world)
    event = ev.create(make_event("Study jam"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    club_invite = ev.invite_co_host(event.id, club.id, world.carol.id, NOW)
    national_invite = ev.invite_co_host(event.id, world.national.id, world.carol.id, NOW)
    ev.accept_co_host(national_invite.id, world.bob.id)
    ev.accept_co_host(club_invite.id, dave.id)

    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == world.national.id


# --- 5. answering a link that was already declined ----------------------------


def test_a_declined_invitation_cannot_be_accepted(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.remove_co_host(invite.id, world.bob.id)
    with pytest.raises((DomainRuleError, NotFoundError)):
        ev.accept_co_host(invite.id, world.bob.id)


def test_a_declined_request_cannot_be_approved(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    world.graph.events.remove_co_host(request.id, world.alice.id)
    with pytest.raises((DomainRuleError, NotFoundError)):
        world.graph.events.accept_co_host(request.id, world.alice.id)


# --- 6-9. behavior that needs a product decision ------------------------------


@pytest.mark.parametrize("action", ["cancel", "archive"])
def test_only_the_primary_host_can_cancel_or_archive(world, action):
    event = with_national_co_host(world)
    with pytest.raises(PermissionDeniedError):
        getattr(world.graph.events, action)(event.id, world.bob.id)


def test_a_host_cannot_approve_their_own_request(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.alice.id, world.alice.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.events.accept_co_host(request.id, world.alice.id)


def test_cancelling_an_event_closes_its_pending_links(world):
    cohost = world.graph.co_hosting
    event = chapter_event(world)
    cohost.request(event.id, world.national.id, world.bob.id, NOW)
    world.graph.events.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    world.graph.events.cancel(event.id, world.alice.id)
    assert cohost.pending_for(world.national.id, world.bob.id) == []
    assert cohost.pending_for(world.carol.id, world.carol.id) == []


def test_calendar_window_keeps_an_event_that_is_under_way(world):
    event = chapter_event(world)
    mid = event.starts_at + timedelta(hours=1)
    listing = world.graph.calendar.host_events(world.chapter.id, None, starts_after=mid)
    assert [e.id for e in listing.public] == [event.id]


def test_promoted_co_host_is_the_only_primary(world):
    """Sanity check alongside test 4: promotion never leaves two primaries."""
    ev = world.graph.events
    event = ev.create(make_event("Study jam"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    invite = ev.invite_co_host(event.id, world.national.id, world.carol.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    world.graph.privacy.erase_person(world.carol.id)
    roles = [h.role for h in ev.host_edges(event.id)]
    assert roles == [HostRole.PRIMARY]
