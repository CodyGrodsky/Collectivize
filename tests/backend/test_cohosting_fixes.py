"""Edge cases around the co-hosting, member-list and calendar fixes (see
test_cohosting_gaps.py for the behaviour each fix delivers), plus a fuzz
test that drives random co-hosting actions and checks the invariants after
every step."""

from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.graph import CollectivizeGraph, NotFoundError, PermissionDeniedError
from backend.graph.store import InMemoryGraphStore
from backend.objects import (
    DomainRuleError,
    EventStatus,
    HostRole,
    Hosts,
    MemberRole,
    OrgKind,
    RecordState,
    Visibility,
)

from .conftest import NOW, make_admin, make_event, make_org, make_person


def chapter_event(world, days=3, **extra):
    event = replace(make_event("Bonfire", days_ahead=days), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def invite_national(world, event):
    return world.graph.events.invite_co_host(event.id, world.national.id, world.alice.id, NOW)


# --- accepting -------------------------------------------------------------------------


def test_accepting_twice_changes_nothing(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = invite_national(world, event)
    first = ev.accept_co_host(invite.id, world.bob.id)
    again = ev.accept_co_host(invite.id, world.bob.id)
    assert again == first


def test_an_invitation_to_an_archived_event_cannot_be_accepted(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = invite_national(world, event)
    ev.archive(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        ev.accept_co_host(invite.id, world.bob.id)


def test_a_withdrawn_invitation_says_why_it_cannot_be_accepted(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = invite_national(world, event)
    ev.remove_co_host(invite.id, world.alice.id)
    with pytest.raises(DomainRuleError, match="withdrawn or declined"):
        ev.accept_co_host(invite.id, world.bob.id)


def test_hosts_approving_someone_elses_request_still_works(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.national.id, world.bob.id, NOW)
    assert world.graph.events.accept_co_host(request.id, world.alice.id).is_effective


def test_an_admin_of_both_sides_cannot_approve_their_own_orgs_request(world):
    make_admin(world.graph, world.bob, world.council)
    event = with_national(world)
    request = world.graph.co_hosting.request(event.id, world.council.id, world.bob.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.events.accept_co_host(request.id, world.bob.id)  # Bob hosts AND asked
    assert world.graph.events.accept_co_host(request.id, world.alice.id).is_effective


def with_national(world):
    event = chapter_event(world)
    world.graph.events.accept_co_host(invite_national(world, event).id, world.bob.id)
    return event


# --- inviting --------------------------------------------------------------------------


def test_drafts_can_still_invite_co_hosts(world):
    draft = world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    assert world.graph.events.invite_co_host(draft.id, world.national.id, world.alice.id, NOW)


def test_inviting_stops_exactly_when_the_event_ends(world):
    event = chapter_event(world)
    ev = world.graph.events
    assert ev.invite_co_host(event.id, world.national.id, world.alice.id,
                             event.ends_at - timedelta(microseconds=1))
    with pytest.raises(DomainRuleError):
        ev.invite_co_host(event.id, world.council.id, world.alice.id, event.ends_at)


def test_cannot_invite_to_a_cancelled_event(world):
    event = chapter_event(world)
    world.graph.events.cancel(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        invite_national(world, event)


# --- withdrawing -----------------------------------------------------------------------


def test_only_the_sender_or_the_primary_can_withdraw_a_pending_invitation(world):
    ev = world.graph.events
    event = with_national(world)
    club = make_org(world.graph, "Chess Club", OrgKind.CLUB)
    dave = make_person(world.graph, "Dave")
    make_admin(world.graph, dave, club)
    invite = ev.invite_co_host(event.id, club.id, world.bob.id, NOW)  # Bob sent it
    with pytest.raises(PermissionDeniedError):
        ev.remove_co_host(invite.id, world.carol.id)
    assert ev.remove_co_host(invite.id, world.bob.id).state is RecordState.REMOVED


def test_the_sender_cannot_remove_an_invitation_once_accepted(world):
    ev = world.graph.events
    event = with_national(world)
    club = make_org(world.graph, "Chess Club", OrgKind.CLUB)
    dave = make_person(world.graph, "Dave")
    make_admin(world.graph, dave, club)
    invite = ev.invite_co_host(event.id, club.id, world.bob.id, NOW)
    ev.accept_co_host(invite.id, dave.id)
    with pytest.raises(PermissionDeniedError):
        ev.remove_co_host(invite.id, world.bob.id)  # now only the club or the primary


# --- cancelling and archiving ------------------------------------------------------------


def test_another_admin_of_the_primary_org_can_still_cancel(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_role(edge.id, MemberRole.ADMIN, world.alice.id)
    event = with_national(world)
    assert world.graph.events.cancel(event.id, world.carol.id).status is EventStatus.CANCELLED


def test_a_co_host_can_still_edit_but_not_cancel(world):
    event = with_national(world)
    ev = world.graph.events
    assert ev.revise(event.id, world.bob.id, NOW, title="Bonfire!").title == "Bonfire!"
    with pytest.raises(PermissionDeniedError):
        ev.cancel(event.id, world.bob.id)


def test_cancelling_keeps_accepted_co_hosts_and_closes_only_pending_links(world):
    ev = world.graph.events
    event = with_national(world)
    pending = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    ev.cancel(event.id, world.alice.id)
    assert ev.host_org_ids(event.id) == {world.chapter.id, world.national.id}
    assert world.graph.store.edge(pending.id, Hosts).state is RecordState.REMOVED


def test_archiving_closes_pending_links_too(world):
    ev = world.graph.events
    event = chapter_event(world)
    pending = invite_national(world, event)
    ev.archive(event.id, world.alice.id)
    assert world.graph.store.edge(pending.id, Hosts).state is RecordState.REMOVED


# --- who takes over after erasure ---------------------------------------------------------


def person_event_with_co_hosts(world, count):
    ev = world.graph.events
    event = ev.create(make_event("Study jam"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    hosts = []
    for i in range(count):
        org = make_org(world.graph, f"Club {i}", OrgKind.CLUB)
        admin = make_person(world.graph, f"Admin {i}")
        make_admin(world.graph, admin, org)
        hosts.append((org, admin, ev.invite_co_host(event.id, org.id, world.carol.id, NOW)))
    return event, hosts


def test_the_earliest_acceptance_wins_even_if_invited_last(world):
    ev = world.graph.events
    event, hosts = person_event_with_co_hosts(world, 3)
    for org, admin, invite in reversed(hosts):  # accept in reverse order
        ev.accept_co_host(invite.id, admin.id)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == hosts[-1][0].id


def test_a_removed_co_host_is_skipped_when_promoting(world):
    ev = world.graph.events
    event, hosts = person_event_with_co_hosts(world, 2)
    for _, admin, invite in hosts:
        ev.accept_co_host(invite.id, admin.id)
    ev.remove_co_host(hosts[0][2].id, hosts[0][1].id)  # the first one leaves
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == hosts[1][0].id


def test_co_hosts_accepted_before_ordering_existed_still_get_promoted(world):
    ev = world.graph.events
    event, hosts = person_event_with_co_hosts(world, 1)
    legacy = replace(hosts[0][2], accepted=True)  # saved without an acceptance order
    world.graph.store.save_edge(legacy)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == hosts[0][0].id


# --- member lists --------------------------------------------------------------------------


def connect(world, a, b):
    world.graph.connections.accept(world.graph.connections.request(a.id, b.id, NOW).id, b.id)


def chapter_member(world, person, visibility):
    m = world.graph.membership
    edge = m.request(person.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_visibility(edge.id, person.id, visibility)


def names(world, viewer):
    return [e.person.display_name
            for e in world.graph.roster.members(world.chapter.id, viewer.id if viewer else None)]


def test_connections_only_members_are_listed_for_their_connections_and_themselves(world):
    chapter_member(world, world.carol, Visibility.CONNECTIONS)
    connect(world, world.carol, world.bob)
    assert names(world, world.bob) == ["Alice", "Carol"]
    assert names(world, world.carol) == ["Alice", "Carol"]
    assert names(world, None) == ["Alice"]


def test_a_default_membership_is_still_on_the_public_list(world):
    chapter_member(world, world.carol, Visibility.MEMBERS)  # the default level
    assert names(world, None) == ["Alice", "Carol"]


def test_a_connections_only_profile_is_honoured_too(world):
    chapter_member(world, world.carol, Visibility.MEMBERS)
    carol = world.graph.store.node(world.carol.id, type(world.carol))
    world.graph.store.save_node(carol.with_visibility(Visibility.CONNECTIONS))
    assert names(world, None) == ["Alice"]


def test_the_network_view_respects_connections_only_memberships(world):
    chapter_member(world, world.carol, Visibility.CONNECTIONS)
    view = world.graph.network.neighborhood(world.bob.id, world.chapter.id)
    assert world.carol.id not in {n.id for n in view.nodes}


# --- host calendar window ------------------------------------------------------------------


def test_window_edges_are_exact(world):
    event = chapter_event(world)
    cal = world.graph.calendar
    assert cal.host_events(world.chapter.id, None, starts_after=event.ends_at).public == ()
    assert cal.host_events(world.chapter.id, None, starts_before=event.starts_at).public == ()
    just_before_end = event.ends_at - timedelta(microseconds=1)
    assert [e.id for e in cal.host_events(world.chapter.id, None,
                                          starts_after=just_before_end).public] == [event.id]


# --- fuzz: random co-hosting actions keep the invariants -----------------------------------

ACTIONS = st.lists(st.tuples(
    st.sampled_from(["invite", "request", "accept", "remove", "cancel", "archive", "erase"]),
    st.integers(0, 3),  # which party
    st.integers(0, 3),  # which actor
), max_size=25)


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ACTIONS)
def test_random_co_hosting_actions_keep_the_rules(actions):
    graph = CollectivizeGraph(InMemoryGraphStore())
    orgs = [make_org(graph, f"Org {i}", OrgKind.CLUB) for i in range(3)]
    admins = [make_person(graph, f"Admin {i}") for i in range(3)]
    for org, admin in zip(orgs, admins, strict=True):
        make_admin(graph, admin, org)
    loner = make_person(graph, "Loner")
    parties = [o.id for o in orgs] + [loner.id]
    people = [a.id for a in admins] + [loner.id]
    event = make_event("Fuzz night")
    graph.events.create(event, orgs[0].id, admins[0].id)
    graph.events.publish(event.id, admins[0].id, NOW)

    for action, party, actor in actions:
        if not graph.store.has_node(event.id):
            break
        links = graph.store.edges(Hosts, target_id=event.id, include_inactive=True)
        link = links[party % len(links)]
        try:
            run(graph, action, event.id, parties[party], people[actor], link)
        except (DomainRuleError, NotFoundError, LookupError):
            pass  # the rules said no; that's fine
        check_invariants(graph, event.id)


def run(graph, action, event_id, party, actor, link):
    ev = graph.events
    if action == "invite":
        ev.invite_co_host(event_id, party, actor, NOW)
    elif action == "request":
        graph.co_hosting.request(event_id, party, actor, NOW)
    elif action == "accept":
        ev.accept_co_host(link.id, actor)
    elif action == "remove":
        ev.remove_co_host(link.id, actor)
    elif action in ("cancel", "archive"):
        getattr(ev, action)(event_id, actor)
    elif action == "erase" and graph.store.has_node(actor):
        graph.privacy.erase_person(actor)


def check_invariants(graph, event_id):
    if not graph.store.has_node(event_id):
        return
    effective = graph.events.host_edges(event_id)
    primaries = [h for h in effective if h.role is HostRole.PRIMARY]
    assert len(primaries) <= 1, "never two primary hosts"
    event = graph.store.any_node(event_id)
    if event.status in (EventStatus.CANCELLED, EventStatus.ARCHIVED):
        pending = [h for h in graph.store.edges(Hosts, target_id=event_id) if not h.accepted]
        assert pending == [], "a cancelled or archived event has no open invitations"
    assert graph.integrity.audit() == ()
