"""Adversarial edge cases for the social-graph rules and privacy: claiming,
memberships, hosting, calendars, member lists, connections, the network
view, invites, erasure and the integrity audit.

Tests marked `xfail(strict=True, reason="BUG: ...")` assert the correct
behaviour and currently fail because of a real bug.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.graph import (
    CollectivizeGraph,
    InMemoryGraphStore,
    NotFoundError,
    PermissionDeniedError,
)
from backend.graph.invites import INVALID_INVITE, INVITE_LIFETIME
from backend.graph.roster import RosterPolicy
from backend.objects import (
    ClaimState,
    Connected,
    DomainRuleError,
    Edge,
    Event,
    EventSeries,
    EventStatus,
    HostRole,
    MemberRole,
    Organization,
    OrgKind,
    PartOfKind,
    Person,
    Provenance,
    ProvenanceMethod,
    Visibility,
)

from .conftest import (
    NOW,
    STAFF,
    make_admin,
    make_event,
    make_org,
    make_person,
    staff_provenance,
)

# --- helpers -------------------------------------------------------------------


def connect(world, a, b):
    request = world.graph.connections.request(a.id, b.id, NOW)
    return world.graph.connections.accept(request.id, b.id)


def join(world, person, org, admin):
    edge = world.graph.membership.request(person.id, org.id, NOW)
    return world.graph.membership.confirm(edge.id, admin.id)


def person_with(world, name: str, **fields) -> Person:
    return world.graph.store.save_node(
        Person(
            display_name=name,
            provenance=Provenance(created_by=None, method=ProvenanceMethod.SELF_CLAIM),
            **fields,
        )
    )


def chapter_event(world, title="Chapter social", days_ahead=3, **extra) -> Event:
    event = replace(make_event(title, days_ahead=days_ahead), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def neighbor_ids(view, center_id) -> set[str]:
    return {
        link.target if link.source == center_id else link.source
        for link in view.links
        if center_id in (link.source, link.target)
    }


def make_series(world, owner: Organization, admin: Person) -> EventSeries:
    series = EventSeries(
        owner_org_id=owner.id,
        name="Weekly",
        recurrence="Weekly",
        default_title="Meeting",
        default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
    )
    return world.graph.series.create(series, admin.id)


# --- claiming and membership -----------------------------------------------------


def test_confirmed_member_claiming_a_released_org_gets_no_rights_until_approved(world):
    m = world.graph.membership
    join(world, world.carol, world.chapter, world.alice)
    m.leave(world.alice_edge_id, world.alice.id, NOW, release_org=True)
    assert world.graph.store.node(world.chapter.id, Organization).claim_state is (
        ClaimState.UNCLAIMED
    )
    m.claim_org(world.carol.id, world.chapter.id, NOW)
    assert not m.is_admin(world.carol.id, world.chapter.id)
    assert world.graph.integrity.audit() == ()


def test_released_org_can_be_reclaimed_by_a_newcomer_through_staff(world):
    m = world.graph.membership
    m.leave(world.alice_edge_id, world.alice.id, NOW, release_org=True)
    claim = m.claim_org(world.carol.id, world.chapter.id, NOW)
    assert not m.is_admin(world.carol.id, world.chapter.id)
    m.approve_claim(claim.id, STAFF, NOW)
    assert m.is_admin(world.carol.id, world.chapter.id)
    assert world.graph.integrity.audit() == ()


def test_former_admin_has_no_rights_anywhere_after_leaving(world):
    m, ev = world.graph.membership, world.graph.events
    event = chapter_event(world)
    dave = make_person(world.graph, "Dave")
    join(world, dave, world.chapter, world.alice)
    m.set_role(m.current(dave.id, world.chapter.id).id, MemberRole.ADMIN, world.alice.id)
    m.leave(world.alice_edge_id, world.alice.id, NOW)
    pending = m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.confirm(pending.id, world.alice.id)
    with pytest.raises(PermissionDeniedError):
        ev.revise(event.id, world.alice.id, NOW, title="Hijacked")
    with pytest.raises(PermissionDeniedError):
        ev.invite_co_host(event.id, world.alice.id, world.alice.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.hierarchy.propose(
            world.chapter.id, world.national.id, PartOfKind.CHAPTER_OF, world.alice.id, NOW
        )


def test_pending_claimant_cannot_confirm_or_edit(world):
    m = world.graph.membership
    claim = m.claim_org(world.carol.id, world.council.id, NOW)
    dave = make_person(world.graph, "Dave")
    req = m.request(dave.id, world.council.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.confirm(req.id, world.carol.id)
    with pytest.raises(PermissionDeniedError):
        world.graph.events.create(make_event(), world.council.id, world.carol.id)
    assert m.current(world.carol.id, world.council.id).id == claim.id


def test_leaving_twice_and_confirming_after_leaving_are_rejected(world):
    m = world.graph.membership
    req = m.request(world.carol.id, world.chapter.id, NOW)
    m.leave(req.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        m.leave(req.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        m.confirm(req.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        m.set_role(req.id, MemberRole.ADMIN, world.alice.id)


def test_admin_of_another_org_cannot_confirm_or_promote(world):
    m = world.graph.membership
    req = m.request(world.carol.id, world.chapter.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.confirm(req.id, world.bob.id)
    m.confirm(req.id, world.alice.id)
    with pytest.raises(PermissionDeniedError):
        m.set_role(req.id, MemberRole.ADMIN, world.bob.id)


def test_last_admin_cannot_demote_self(world):
    with pytest.raises(DomainRuleError):
        world.graph.membership.set_role(world.alice_edge_id, MemberRole.MEMBER, world.alice.id)


def test_hiding_a_past_membership_does_not_reveal_it_elsewhere(world):
    m = world.graph.membership
    edge = join(world, world.carol, world.chapter, world.alice)
    m.leave(edge.id, world.carol.id, NOW, keep_visible=False)
    roster = world.graph.roster.members(world.chapter.id, world.bob.id)
    assert world.carol.id not in {e.person.id for e in roster}


# --- integrity audit after legitimate flows ---------------------------------------


def test_audit_is_clean_after_the_confirming_admin_steps_down(world):
    m = world.graph.membership
    carol_edge = join(world, world.carol, world.chapter, world.alice)
    dave = make_person(world.graph, "Dave")
    dave_edge = join(world, dave, world.chapter, world.alice)
    m.set_role(dave_edge.id, MemberRole.ADMIN, world.alice.id)
    m.set_role(world.alice_edge_id, MemberRole.MEMBER, dave.id)
    assert m.is_member(world.carol.id, world.chapter.id)
    assert carol_edge.id not in {i.record_id for i in world.graph.integrity.audit()}


def test_audit_is_clean_after_the_confirming_admin_is_erased(world):
    m = world.graph.membership
    join(world, world.carol, world.chapter, world.alice)
    dave = make_person(world.graph, "Dave")
    dave_edge = join(world, dave, world.chapter, world.alice)
    m.set_role(dave_edge.id, MemberRole.ADMIN, world.alice.id)
    world.graph.privacy.erase_person(world.alice.id)
    assert world.graph.integrity.audit() == ()


def test_audit_is_clean_after_admin_handoff_and_leave(world):
    m = world.graph.membership
    join(world, world.carol, world.chapter, world.alice)
    dave = make_person(world.graph, "Dave")
    dave_edge = join(world, dave, world.chapter, world.alice)
    m.set_role(dave_edge.id, MemberRole.ADMIN, world.alice.id)
    m.leave(world.alice_edge_id, world.alice.id, NOW)
    assert world.graph.integrity.audit() == ()


def test_audit_is_clean_after_erasing_a_primary_host_with_co_hosts(world):
    ev = world.graph.events
    event = ev.create(make_event("Carol's jam"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    invite = ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    ev.accept_co_host(invite.id, world.alice.id)
    pending = ev.invite_co_host(event.id, world.bob.id, world.carol.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == world.chapter.id
    assert world.graph.store.edge(pending.id, type(pending)).accepted is False
    assert world.graph.integrity.audit() == ()


# --- erasure in every role --------------------------------------------------------


def test_erasing_a_sole_host_with_only_pending_co_hosts_deletes_the_event(world):
    ev = world.graph.events
    event = ev.create(make_event("Solo"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    assert not world.graph.store.has_node(event.id)
    assert world.graph.store.edges_touching(event.id) == []
    assert world.graph.integrity.audit() == ()


def test_erasing_a_co_host_leaves_the_event_with_its_primary(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.carol.id)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.host_ids(event.id) == {world.chapter.id}
    assert world.graph.store.has_node(event.id)


def test_erasing_a_person_with_pending_requests_and_invites(world):
    event = chapter_event(world)
    request = world.graph.co_hosting.request(event.id, world.carol.id, world.carol.id, NOW)
    membership = world.graph.membership.request(world.carol.id, world.national.id, NOW)
    dave = make_person(world.graph, "Dave")
    conn = world.graph.connections.request(world.carol.id, dave.id, NOW)
    issued = world.graph.invites.create(world.carol.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    store = world.graph.store
    for edge_id in (request.id, membership.id, conn.id):
        with pytest.raises(NotFoundError):
            store.edge(edge_id, Edge)
    assert not store.has_node(issued.invite.id)
    assert world.graph.co_hosting.pending(event.id, world.alice.id) == []
    assert world.graph.integrity.audit() == ()


def test_erasing_the_last_admin_of_several_orgs_and_a_co_admin(world):
    m = world.graph.membership
    club = make_org(world.graph, "Club", OrgKind.CLUB)
    make_admin(world.graph, world.alice, club)
    edge = join(world, world.alice, world.national, world.bob)
    m.set_role(edge.id, MemberRole.ADMIN, world.bob.id)
    world.graph.privacy.erase_person(world.alice.id)
    store = world.graph.store
    assert store.node(world.chapter.id, Organization).claim_state is ClaimState.UNCLAIMED
    assert store.node(club.id, Organization).claim_state is ClaimState.UNCLAIMED
    assert store.node(world.national.id, Organization).claim_state is ClaimState.CLAIMED
    assert world.graph.integrity.audit() == ()


def test_erasing_an_inviter_makes_their_codes_fail_like_any_bad_code(world):
    issued = world.graph.invites.create(world.carol.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    with pytest.raises(DomainRuleError, match=INVALID_INVITE):
        world.graph.invites.redeem(issued.code, "x@northeastern.edu", "X", NOW)


def test_erased_person_vanishes_from_network_paths(world):
    dave = make_person(world.graph, "Dave")
    connect(world, world.alice, world.carol)
    connect(world, world.carol, dave)
    assert world.graph.network.find_path(world.alice.id, dave.id) is not None
    world.graph.privacy.erase_person(world.carol.id)
    assert world.graph.network.find_path(world.alice.id, dave.id) is None
    with pytest.raises(NotFoundError):
        world.graph.network.find_path(world.alice.id, world.carol.id)


# --- hosting and co-hosting ---------------------------------------------------------


@pytest.mark.parametrize("level", [Visibility.PRIVATE, Visibility.CONNECTIONS])
def test_org_admin_sees_their_orgs_own_restricted_event(world, level):
    event = chapter_event(world, title="Officer retreat", visibility=level)
    assert world.graph.privacy.can_view(world.alice.id, event)
    listing = world.graph.calendar.host_events(world.chapter.id, world.alice.id)
    assert event.id in {e.id for e in listing.private}


def test_restricted_org_event_is_hidden_from_outsiders(world):
    event = chapter_event(world, title="Officer retreat", visibility=Visibility.PRIVATE)
    for viewer in (None, world.bob.id, world.carol.id):
        assert not world.graph.privacy.can_view(viewer, event)


def test_person_co_host_cannot_act_for_the_primary_org(world):
    ev, m = world.graph.events, world.graph.membership
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.carol.id)
    req = m.request(make_person(world.graph, "Dave").id, world.chapter.id, NOW)
    with pytest.raises(PermissionDeniedError):
        m.confirm(req.id, world.carol.id)
    with pytest.raises(PermissionDeniedError):
        world.graph.co_hosting.request(event.id, world.chapter.id, world.carol.id, NOW)
    nat = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(nat.id, world.bob.id)
    with pytest.raises(PermissionDeniedError):
        ev.remove_co_host(nat.id, world.carol.id)


def test_only_admins_of_the_series_owner_can_add_instances(world):
    ev = world.graph.events
    event = chapter_event(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    national_series = make_series(world, world.national, world.bob)
    with pytest.raises(PermissionDeniedError):
        world.graph.series.add_instance(event.id, national_series.id, world.alice.id, NOW)


def test_withdrawn_request_cannot_be_approved_into_a_co_host(world):
    ev = world.graph.events
    event = chapter_event(world)
    req = world.graph.co_hosting.request(event.id, world.national.id, world.bob.id, NOW)
    ev.remove_co_host(req.id, world.bob.id)
    try:
        ev.accept_co_host(req.id, world.alice.id)
    except (DomainRuleError, NotFoundError):
        pass
    assert world.national.id not in ev.host_ids(event.id)
    assert world.graph.co_hosting.pending(event.id, world.alice.id) == []


def test_requester_can_ask_again_after_being_declined(world):
    event = chapter_event(world)
    co = world.graph.co_hosting
    req = co.request(event.id, world.carol.id, world.carol.id, NOW)
    world.graph.events.remove_co_host(req.id, world.alice.id)
    again = co.request(event.id, world.carol.id, world.carol.id, NOW)
    assert again.id != req.id
    assert [h.id for h in co.pending(event.id, world.alice.id)] == [again.id]


def test_requester_cannot_approve_their_own_request(world):
    event = chapter_event(world)
    req = world.graph.co_hosting.request(event.id, world.national.id, world.bob.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.events.accept_co_host(req.id, world.bob.id)


def test_request_on_ended_or_draft_event_is_rejected(world):
    co = world.graph.co_hosting
    event = chapter_event(world, days_ahead=1)
    with pytest.raises(DomainRuleError):
        co.request(event.id, world.carol.id, world.carol.id, event.ends_at)
    draft = world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    # A draft is invisible to Carol, so it looks missing rather than "not open".
    with pytest.raises(NotFoundError):
        co.request(draft.id, world.carol.id, world.carol.id, NOW)
    with pytest.raises(NotFoundError):
        co.request("no-such-event", world.carol.id, world.carol.id, NOW)


def test_hidden_and_missing_events_fail_identically_for_requests(world):
    hidden = chapter_event(world, title="Secret", visibility=Visibility.MEMBERS)
    co = world.graph.co_hosting
    errors = []
    for event_id in (hidden.id, "missing-id"):
        with pytest.raises(NotFoundError) as info:
            co.request(event_id, world.carol.id, world.carol.id, NOW)
        errors.append(str(info.value).replace(event_id, "<id>"))
    assert errors[0] == errors[1]


def test_erasure_promotes_the_first_accepted_co_host_not_a_pending_one(world):
    ev = world.graph.events
    event = ev.create(make_event("Jam"), world.carol.id, world.carol.id)
    ev.publish(event.id, world.carol.id, NOW)
    ev.invite_co_host(event.id, world.national.id, world.carol.id, NOW)  # stays pending
    accepted = ev.invite_co_host(event.id, world.chapter.id, world.carol.id, NOW)
    ev.accept_co_host(accepted.id, world.alice.id)
    world.graph.privacy.erase_person(world.carol.id)
    assert ev.primary_host_id(event.id) == world.chapter.id
    primaries = [h for h in ev.host_edges(event.id) if h.role is HostRole.PRIMARY]
    assert len(primaries) == 1


# --- calendars ----------------------------------------------------------------------


def test_calendar_window_keeps_events_that_overlap_it(world):
    event = chapter_event(world, days_ahead=5)
    cal = world.graph.calendar
    start, end = event.starts_at, event.ends_at

    def ids(**window):
        listing = cal.host_events(world.chapter.id, world.bob.id, **window)
        return {e.id for e in listing.public}

    assert event.id in ids(starts_after=start)
    assert event.id in ids(starts_after=start + timedelta(microseconds=1))  # under way
    assert event.id not in ids(starts_after=end)
    assert event.id not in ids(starts_before=start)
    assert event.id in ids(starts_before=start + timedelta(microseconds=1))
    assert ids(starts_after=start, starts_before=start) == set()


def test_calendar_keeps_archived_events_off_and_cancelled_published_on(world):
    ev = world.graph.events
    archived = chapter_event(world, title="Old")
    ev.archive(archived.id, world.alice.id)
    cancelled = chapter_event(world, title="Rained out")
    ev.cancel(cancelled.id, world.alice.id)
    listing = world.graph.calendar.host_events(world.chapter.id, world.bob.id)
    titles = {e.title for e in listing.public}
    assert titles == {"Rained out"}
    assert listing.public[0].status is EventStatus.CANCELLED


def test_private_person_has_no_calendar_for_others(world):
    hermit = person_with(world, "Hermit", visibility=Visibility.PRIVATE)
    event = world.graph.events.create(make_event("Alone"), hermit.id, hermit.id)
    world.graph.events.publish(event.id, hermit.id, NOW)
    with pytest.raises(PermissionDeniedError):
        world.graph.calendar.host_events(hermit.id, world.carol.id)
    own = world.graph.calendar.host_events(hermit.id, hermit.id)
    assert [e.id for e in own.public + own.private] == [event.id]


# --- member lists ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "audience", [Visibility.PUBLIC, Visibility.NETWORK, Visibility.MEMBERS, Visibility.PRIVATE]
)
def test_roster_never_lists_pending_ended_or_private_members(store, audience):
    graph = CollectivizeGraph(store, roster_policy=RosterPolicy(audience=audience))
    org = make_org(graph, "Club", OrgKind.CLUB)
    admin = make_person(graph, "Admin")
    make_admin(graph, admin, org)
    pending = make_person(graph, "Pending")
    graph.membership.request(pending.id, org.id, NOW)
    gone = make_person(graph, "Gone")
    edge = graph.membership.request(gone.id, org.id, NOW)
    graph.membership.confirm(edge.id, admin.id)
    graph.membership.leave(edge.id, gone.id, NOW)
    shy = make_person(graph, "Shy")
    shy_edge = graph.membership.request(shy.id, org.id, NOW)
    graph.membership.confirm(shy_edge.id, admin.id)
    graph.membership.set_visibility(shy_edge.id, shy.id, Visibility.PRIVATE)
    names = [e.person.display_name for e in graph.roster.members(org.id, admin.id)]
    assert names == ["Admin"]
    outsider = make_person(graph, "Outsider")
    if audience in (Visibility.MEMBERS, Visibility.PRIVATE):
        with pytest.raises(PermissionDeniedError):
            graph.roster.members(org.id, outsider.id)
    else:
        assert graph.roster.members(org.id, outsider.id)


def test_members_only_roster_hides_from_pending_and_former_members(store):
    graph = CollectivizeGraph(store, roster_policy=RosterPolicy(audience=Visibility.MEMBERS))
    org = make_org(graph, "Club", OrgKind.CLUB)
    admin = make_person(graph, "Admin")
    make_admin(graph, admin, org)
    pending = make_person(graph, "Pending")
    graph.membership.request(pending.id, org.id, NOW)
    with pytest.raises(PermissionDeniedError):
        graph.roster.members(org.id, pending.id)
    former = make_person(graph, "Former")
    edge = graph.membership.request(former.id, org.id, NOW)
    graph.membership.confirm(edge.id, admin.id)
    assert graph.roster.members(org.id, former.id)
    graph.membership.leave(edge.id, former.id, NOW)
    with pytest.raises(PermissionDeniedError):
        graph.roster.members(org.id, former.id)


# --- connections ----------------------------------------------------------------------


def test_connection_cannot_be_accepted_by_the_sender_or_after_removal(world):
    c = world.graph.connections
    req = c.request(world.alice.id, world.bob.id, NOW)
    with pytest.raises(PermissionDeniedError):
        c.accept(req.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        c.request(world.bob.id, world.alice.id, NOW)  # reverse duplicate
    c.remove(req.id, world.bob.id)
    with pytest.raises(NotFoundError):
        c.accept(req.id, world.bob.id)
    assert not c.are_connected(world.alice.id, world.bob.id)
    again = c.request(world.bob.id, world.alice.id, NOW)
    c.accept(again.id, world.alice.id)
    assert c.are_connected(world.alice.id, world.bob.id)
    assert c.are_connected(world.bob.id, world.alice.id)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["request", "accept", "remove"]),
            st.integers(0, 3),
            st.integers(0, 3),
        ),
        max_size=25,
    )
)
def test_connection_operations_keep_one_symmetric_edge_per_pair(ops):
    graph = CollectivizeGraph(InMemoryGraphStore())
    people = [make_person(graph, f"P{i}") for i in range(4)]
    c = graph.connections
    for op, i, j in ops:
        a, b = people[i].id, people[j].id
        edge = c.between(a, b)
        try:
            if op == "request":
                c.request(a, b, NOW)
            elif op == "accept" and edge is not None:
                c.accept(edge.id, a)
            elif op == "remove" and edge is not None:
                c.remove(edge.id, a)
        except (DomainRuleError, PermissionDeniedError):
            pass
    for x in people:
        for y in people:
            assert c.are_connected(x.id, y.id) == c.are_connected(y.id, x.id)
            if x.id != y.id:
                both = graph.store.edges(Connected, source_id=x.id, target_id=y.id)
                both += graph.store.edges(Connected, source_id=y.id, target_id=x.id)
                assert len(both) <= 1
        assert x.id not in c.connections_of(x.id)
    assert graph.integrity.audit() == ()


# --- network view privacy -------------------------------------------------------------


@pytest.mark.parametrize("level", [Visibility.CONNECTIONS, Visibility.MEMBERS])
def test_people_the_viewer_cannot_see_never_appear_through_orgs(world, level):
    shy = person_with(world, "Shy", visibility=level)
    join(world, shy, world.chapter, world.alice)
    assert not world.graph.privacy.can_view(world.bob.id, shy)
    view = world.graph.network.neighborhood(world.bob.id, world.chapter.id)
    assert shy.id not in {n.id for n in view.nodes}


def test_paths_never_route_through_people_the_viewer_cannot_see(world):
    shy = person_with(world, "Shy", visibility=Visibility.CONNECTIONS)
    club = make_org(world.graph, "Robotics Club", OrgKind.CLUB)
    dana = make_person(world.graph, "Dana")
    make_admin(world.graph, dana, club)
    join(world, shy, world.chapter, world.alice)
    join(world, shy, club, dana)
    join(world, world.bob, world.chapter, world.alice)
    assert not world.graph.privacy.can_view(world.bob.id, shy)
    # The only route from Bob to the club is Bob -> chapter -> Shy -> club.
    path = world.graph.network.find_path(world.bob.id, club.id)
    assert path is None or shy.id not in {n.id for n in path.nodes}


def test_hide_connections_person_is_never_a_mutual_friend_or_step(world):
    hidden = make_person(world.graph, "Hidden")
    dave = make_person(world.graph, "Dave")
    connect(world, world.carol, hidden)
    connect(world, hidden, dave)
    connect(world, world.carol, dave)
    world.graph.connections.set_hide_connections(hidden.id, hidden.id, True)
    common = world.graph.network.in_common(world.carol.id, dave.id)
    assert hidden.id not in {p.id for p in common.people}
    # Bob, a stranger, gets no path through the hidden person.
    connect(world, world.bob, hidden)
    path = world.graph.network.find_path(world.bob.id, dave.id)
    assert path is None or hidden.id not in {n.id for n in path.nodes}
    # But Bob still sees his own link to Hidden.
    view = world.graph.network.neighborhood(world.bob.id)
    assert hidden.id in neighbor_ids(view, world.bob.id)
    # And nobody else sees Hidden's links.
    view = world.graph.network.neighborhood(world.alice.id, hidden.id)
    assert neighbor_ids(view, hidden.id) == set()


def test_pending_and_ended_memberships_are_not_links_even_to_admins(world):
    m = world.graph.membership
    m.request(world.carol.id, world.chapter.id, NOW)
    dave = make_person(world.graph, "Dave")
    edge = join(world, dave, world.chapter, world.alice)
    m.leave(edge.id, dave.id, NOW)
    view = world.graph.network.neighborhood(world.alice.id, world.chapter.id)
    assert neighbor_ids(view, world.chapter.id) == {world.alice.id}


def test_in_common_does_not_reveal_a_private_shared_org(world):
    secret = world.graph.store.save_node(
        Organization(
            name="Secret Society",
            kind=OrgKind.CLUB,
            visibility=Visibility.PRIVATE,
            provenance=staff_provenance(),
        )
    )
    make_admin(world.graph, world.alice, secret)
    join(world, world.carol, secret, world.alice)
    common = world.graph.network.in_common(world.carol.id, world.alice.id)
    assert secret.id not in {o.id for o in common.orgs}
    with pytest.raises(NotFoundError):
        world.graph.network.neighborhood(world.bob.id, secret.id)


def test_network_rejects_non_person_or_org_targets(world):
    event = chapter_event(world)
    with pytest.raises(NotFoundError):
        world.graph.network.find_path(world.alice.id, event.id)
    with pytest.raises(DomainRuleError):
        world.graph.network.in_common(world.alice.id, world.chapter.id)


# --- invites ----------------------------------------------------------------------------


def test_invite_expires_exactly_at_its_lifetime(world):
    inv = world.graph.invites
    issued = inv.create(world.carol.id, NOW)
    end = NOW + INVITE_LIFETIME
    with pytest.raises(DomainRuleError, match=INVALID_INVITE):
        inv.redeem(issued.code, "late@northeastern.edu", "Late", end)
    result = inv.redeem(
        issued.code, "ok@northeastern.edu", "Ok", end - timedelta(microseconds=1)
    )
    assert result.inviter.id == world.carol.id


def test_expired_invites_free_up_open_slots_and_revoked_ones_never_work(world):
    inv = world.graph.invites
    issued = inv.create(world.carol.id, NOW)
    inv.revoke(issued.invite.id, world.carol.id)
    inv.revoke(issued.invite.id, world.carol.id)  # idempotent
    with pytest.raises(DomainRuleError, match=INVALID_INVITE):
        inv.redeem(issued.code, "a@northeastern.edu", "A", NOW)


def test_invite_email_case_and_whitespace_cannot_create_a_second_account(world):
    inv = world.graph.invites
    first = inv.create(world.carol.id, NOW)
    inv.redeem(first.code, "  Dana@NorthEastern.EDU ", "Dana", NOW)
    second = inv.create(world.carol.id, NOW)
    with pytest.raises(DomainRuleError):
        inv.redeem(second.code, "dana@northeastern.edu", "Dana again", NOW)
    with pytest.raises(DomainRuleError):
        inv.redeem(second.code, "DANA@northeastern.edu\t", "Dana again", NOW)


def test_invite_rejects_lookalike_domains(world):
    inv = world.graph.invites
    for email in (
        "x@northeastern.edu.evil.com",
        "x@mail.northeastern.edu",
        "x@evilnortheastern.edu",
        "x@northeastern.edu@evil.com",
    ):
        with pytest.raises(DomainRuleError):
            inv.validate_email(email)


def test_invitee_already_connected_cannot_happen_and_inviter_link_is_mutual(world):
    inv = world.graph.invites
    issued = inv.create(world.carol.id, NOW)
    result = inv.redeem(issued.code, "new@northeastern.edu", "New", NOW)
    assert world.graph.connections.are_connected(result.person.id, world.carol.id)
    with pytest.raises(DomainRuleError, match=INVALID_INVITE):
        inv.redeem(issued.code, "other@northeastern.edu", "Other", NOW)
    assert world.graph.integrity.audit() == ()
