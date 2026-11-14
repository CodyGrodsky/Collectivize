"""People and orgs can both host; combined events need the other party to
accept; every host's calendar shows the event; member lists follow one
easily changed policy."""

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.graph import CollectivizeGraph, PermissionDeniedError
from backend.graph.discovery import DiscoveryQuery
from backend.graph.roster import RosterPolicy
from backend.objects import (
    DomainRuleError,
    Event,
    MemberRole,
    Organization,
    Visibility,
)

from .conftest import NOW, make_event, make_person


def personal_event(world, title="Study jam") -> Event:
    host = world.carol
    event = world.graph.events.create(make_event(title), host.id, host.id)
    return world.graph.events.publish(event.id, host.id, NOW)


def calendar_titles(world, host, viewer=None) -> tuple[list[str], list[str]]:
    listing = world.graph.calendar.host_events(host.id, viewer.id if viewer else None)
    return [e.title for e in listing.public], [e.title for e in listing.private]


# --- people can host ----------------------------------------------------------


def test_a_person_can_host_their_own_event(world):
    event = personal_event(world)
    ev = world.graph.events
    assert ev.primary_host_id(event.id) == world.carol.id
    assert ev.host_ids(event.id) == {world.carol.id}
    assert ev.host_org_ids(event.id) == set()
    assert ev.can_edit(world.carol.id, event.id)
    assert not ev.can_edit(world.alice.id, event.id)


def test_nobody_can_create_an_event_in_someone_elses_name(world):
    with pytest.raises(PermissionDeniedError):
        world.graph.events.create(make_event(), world.carol.id, world.alice.id)
    with pytest.raises(PermissionDeniedError):
        world.graph.events.create(make_event(), world.chapter.id, world.carol.id)


def test_person_hosted_event_is_discoverable_and_explained(world):
    personal_event(world)
    page = world.graph.discovery.search(DiscoveryQuery(viewer_id=world.bob.id), NOW)
    item = page.items[0]
    assert item.host == world.graph.store.node(world.carol.id, type(world.carol))
    assert item.host_freshness is None
    assert "Hosted by a person" in item.reasons


# --- combined events need both parties ---------------------------------------


def test_person_invites_org_and_only_an_org_admin_can_accept(world):
    ev = world.graph.events
    event = personal_event(world)
    invite = ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    for outsider in (world.carol, world.bob):
        with pytest.raises(PermissionDeniedError):
            ev.accept_co_host(invite.id, outsider.id)
    ev.accept_co_host(invite.id, world.alice.id)
    assert ev.host_ids(event.id) == {world.carol.id, world.chapter.id}


def test_org_invites_person_and_only_that_person_can_accept(world):
    ev = world.graph.events
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    invite = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    for outsider in (world.alice, world.bob):
        with pytest.raises(PermissionDeniedError):
            ev.accept_co_host(invite.id, outsider.id)
    ev.accept_co_host(invite.id, world.carol.id)
    assert ev.can_edit(world.carol.id, event.id)


def test_an_event_can_have_several_people_and_orgs_as_hosts(world):
    ev = world.graph.events
    dana = make_person(world.graph, "Dana")
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    for host, accepter in ((world.national, world.bob), (world.carol, world.carol),
                           (dana, dana)):
        invite = ev.invite_co_host(event.id, host.id, world.alice.id, NOW)
        ev.accept_co_host(invite.id, accepter.id)
    assert ev.host_ids(event.id) == {
        world.chapter.id, world.national.id, world.carol.id, dana.id,
    }
    assert ev.host_org_ids(event.id) == {world.chapter.id, world.national.id}


def test_invited_person_can_decline(world):
    ev = world.graph.events
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    invite = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    ev.remove_co_host(invite.id, world.carol.id)
    assert ev.host_ids(event.id) == {world.chapter.id}


def test_only_hosts_and_orgs_can_be_invited(world):
    from backend.objects import Tag

    from .conftest import staff_provenance

    tag = world.graph.store.save_node(Tag(slug="x", label="X", provenance=staff_provenance()))
    event = personal_event(world)
    with pytest.raises(LookupError):
        world.graph.events.invite_co_host(event.id, tag.id, world.carol.id, NOW)


# --- calendars and event lists -----------------------------------------------


def test_combined_event_shows_on_every_hosts_calendar_once_accepted(world):
    ev = world.graph.events
    event = personal_event(world, "Combined social")
    invite = ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    assert calendar_titles(world, world.chapter, world.bob) == ([], [])
    ev.accept_co_host(invite.id, world.alice.id)
    assert calendar_titles(world, world.chapter, world.bob) == (["Combined social"], [])
    assert calendar_titles(world, world.carol, world.bob) == (["Combined social"], [])

    ev.remove_co_host(invite.id, world.alice.id)
    assert calendar_titles(world, world.chapter, world.bob) == ([], [])


def test_event_lists_split_public_and_private_and_respect_the_viewer(world):
    ev = world.graph.events
    open_event = ev.create(make_event("Open house"), world.chapter.id, world.alice.id)
    ev.publish(open_event.id, world.alice.id, NOW)
    members_only = replace(make_event("Chapter vote", days_ahead=4),
                           visibility=Visibility.MEMBERS)
    closed = ev.create(members_only, world.chapter.id, world.alice.id)
    ev.publish(closed.id, world.alice.id, NOW)
    ev.create(make_event("Unpublished formal", days_ahead=5), world.chapter.id,
              world.alice.id)

    assert calendar_titles(world, world.chapter) == (["Open house"], [])
    assert calendar_titles(world, world.chapter, world.carol) == (["Open house"], [])
    assert calendar_titles(world, world.chapter, world.alice) == (
        ["Open house"], ["Chapter vote", "Unpublished formal"],
    )


def test_calendar_can_be_limited_to_a_date_range(world):
    ev = world.graph.events
    for title, days in (("Soon", 1), ("Later", 20)):
        event = ev.create(make_event(title, days_ahead=days), world.chapter.id, world.alice.id)
        ev.publish(event.id, world.alice.id, NOW)
    listing = world.graph.calendar.host_events(
        world.chapter.id, None, starts_after=NOW, starts_before=NOW + timedelta(days=7)
    )
    assert [e.title for e in listing.public] == ["Soon"]


def test_a_hidden_hosts_calendar_is_hidden(world):
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    with pytest.raises(PermissionDeniedError):
        world.graph.calendar.host_events(world.chapter.id, world.carol.id)


# --- erasing a host -----------------------------------------------------------


def test_erasing_a_solo_host_removes_their_events(world):
    event = personal_event(world)
    world.graph.privacy.erase_person(world.carol.id)
    assert not world.graph.store.has_node(event.id)
    assert world.graph.integrity.audit() == ()


def test_erasing_a_primary_host_hands_the_event_to_a_co_host(world):
    ev = world.graph.events
    event = personal_event(world)
    invite = ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    ev.accept_co_host(invite.id, world.alice.id)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == world.chapter.id
    assert world.graph.integrity.audit() == ()


# --- member lists -------------------------------------------------------------


def roster_names(graph, org, viewer=None) -> list[str]:
    viewer_id = viewer.id if viewer else None
    return [entry.person.display_name for entry in graph.roster.members(org.id, viewer_id)]


def test_member_list_is_public_by_default_and_lists_only_confirmed_members(world):
    m = world.graph.membership
    officer = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(officer.id, world.alice.id)
    m.set_role(officer.id, MemberRole.OFFICER, world.alice.id)
    m.request(make_person(world.graph, "Pending Pat").id, world.chapter.id, NOW)

    entries = world.graph.roster.members(world.chapter.id, None)
    assert [(e.person.display_name, e.role) for e in entries] == [
        ("Alice", MemberRole.ADMIN), ("Carol", MemberRole.OFFICER),
    ]


def test_people_who_made_their_membership_private_are_left_off(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_visibility(edge.id, world.carol.id, Visibility.PRIVATE)
    assert roster_names(world.graph, world.chapter) == ["Alice"]

    show_everyone = CollectivizeGraph(
        world.graph.store, roster_policy=RosterPolicy(hide_private_memberships=False)
    )
    assert roster_names(show_everyone, world.chapter) == ["Alice", "Carol"]


@pytest.mark.parametrize(("audience", "allowed", "denied"), [
    (Visibility.NETWORK, "carol", None),
    (Visibility.MEMBERS, "alice", "carol"),
    (Visibility.PRIVATE, "alice", "carol"),
])
def test_who_can_see_member_lists_is_one_policy_setting(world, audience, allowed, denied):
    graph = CollectivizeGraph(world.graph.store, roster_policy=RosterPolicy(audience=audience))
    assert roster_names(graph, world.chapter, getattr(world, allowed)) == ["Alice"]
    with pytest.raises(PermissionDeniedError):
        roster_names(graph, world.chapter, getattr(world, denied) if denied else None)


def test_member_list_is_never_more_visible_than_the_org(world):
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    with pytest.raises(PermissionDeniedError):
        world.graph.roster.members(world.chapter.id, world.carol.id)


def test_connections_only_is_not_a_valid_member_list_audience():
    with pytest.raises(DomainRuleError):
        RosterPolicy(audience=Visibility.CONNECTIONS)

