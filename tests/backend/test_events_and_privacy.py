from datetime import timedelta

import pytest

from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import (
    ClaimState,
    Connected,
    DomainRuleError,
    Event,
    EventSeries,
    EventStatus,
    MemberOf,
    MemberRole,
    Organization,
    Tag,
    Visibility,
)

from .conftest import NOW, make_admin, make_event, make_person, staff_provenance


def create_published(world) -> Event:
    event = world.graph.events.create(make_event(), world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


# --- events ------------------------------------------------------------------


def test_only_host_admin_can_create_and_edit(world):
    ev = world.graph.events
    with pytest.raises(PermissionDeniedError):
        ev.create(make_event(), world.chapter.id, world.carol.id)
    event = create_published(world)
    with pytest.raises(PermissionDeniedError):
        ev.revise(event.id, world.bob.id, NOW, title="Hijacked")


def test_publishing_refreshes_host_org_activity(world):
    create_published(world)
    org = world.graph.store.node(world.chapter.id, Organization)
    assert org.last_event_published_at == NOW


def test_co_host_counts_only_after_accepting(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    assert not ev.can_edit(world.bob.id, event.id)
    ev.accept_co_host(invite.id, world.bob.id)
    assert ev.can_edit(world.bob.id, event.id)
    assert ev.primary_host_id(event.id) == world.chapter.id


def test_co_host_can_remove_itself_but_primary_cannot_be_removed(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    ev.remove_co_host(invite.id, world.bob.id)
    assert ev.host_org_ids(event.id) == {world.chapter.id}
    primary = ev.host_edges(event.id)[0]
    with pytest.raises(DomainRuleError):
        ev.remove_co_host(primary.id, world.alice.id)


def test_tagging_requires_admin_and_existing_tag(world):
    tag = world.graph.store.save_node(
        Tag(slug="philanthropy", label="Philanthropy", provenance=staff_provenance())
    )
    with pytest.raises(PermissionDeniedError):
        world.graph.events.tag(world.chapter.id, tag.id, world.carol.id, NOW)
    with pytest.raises(NotFoundError):
        world.graph.events.tag(world.chapter.id, "missing", world.alice.id, NOW)
    first = world.graph.events.tag(world.chapter.id, tag.id, world.alice.id, NOW)
    again = world.graph.events.tag(world.chapter.id, tag.id, world.alice.id, NOW)
    assert first.id == again.id


# --- series ------------------------------------------------------------------


def make_series(world) -> EventSeries:
    series = EventSeries(
        owner_org_id=world.chapter.id,
        name="Weekly meeting",
        recurrence="Weekly",
        default_title="Chapter meeting",
        default_location="Hall",
        default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
    )
    return world.graph.series.create(series, world.alice.id)


def test_publish_occurrences_in_one_action(world):
    series = make_series(world)
    starts = [NOW + timedelta(days=7 * i) for i in range(1, 4)]
    events = world.graph.series.publish_occurrences(series.id, starts, world.alice.id, NOW)
    assert len(world.graph.series.instances(series.id)) == 3
    with pytest.raises(DomainRuleError):
        world.graph.series.add_instance(events[0].id, series.id, world.alice.id, NOW)


def test_series_edit_updates_only_future_inheriting_instances(world):
    s = world.graph.series
    series = make_series(world)
    first, second = s.publish_occurrences(
        series.id, [NOW + timedelta(days=1), NOW + timedelta(days=8)], world.alice.id, NOW
    )
    world.graph.events.revise(second.id, world.alice.id, NOW, location="Gym")
    later = NOW + timedelta(days=2)  # first has already happened
    s.update_defaults(series.id, world.alice.id, later, location="Library")
    store = world.graph.store
    assert store.node(first.id, Event).location == "Hall"
    assert store.node(second.id, Event).location == "Gym"  # override kept


# --- privacy -----------------------------------------------------------------


def test_edge_visibility_is_most_restrictive_of_endpoints(world):
    m = world.graph.membership
    m.set_visibility(world.alice_edge_id, world.alice.id, Visibility.PUBLIC)
    private_org = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(private_org)
    edge = world.graph.store.edge(world.alice_edge_id, MemberOf)
    p = world.graph.privacy
    assert p.effective_visibility(edge) is Visibility.MEMBERS
    assert not p.can_view(world.carol.id, edge)
    assert not p.can_view(None, edge)
    assert p.can_view(world.alice.id, edge)


def test_draft_events_visible_only_to_host_admins(world):
    event = world.graph.events.create(make_event(), world.chapter.id, world.alice.id)
    p = world.graph.privacy
    assert p.can_view(world.alice.id, event)
    assert not p.can_view(world.carol.id, event)
    assert not p.can_view(None, event)


def test_erasure_hard_deletes_edges_and_releases_orgs(world):
    world.graph.privacy.erase_person(world.alice.id)
    store = world.graph.store
    assert not store.has_node(world.alice.id)
    assert store.edges_touching(world.alice.id) == []
    assert store.node(world.chapter.id, Organization).claim_state is ClaimState.UNCLAIMED


def test_unrelated_person_record_lookup_fails_cleanly(world):
    stranger = make_person(world.graph, "Dana")
    world.graph.privacy.erase_person(stranger.id)
    with pytest.raises(NotFoundError):
        world.graph.privacy.erase_person(stranger.id)


# --- event edge cases --------------------------------------------------------


def make_tag(world, slug: str = "philanthropy") -> Tag:
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


def test_new_events_must_start_as_drafts(world):
    with pytest.raises(DomainRuleError):
        world.graph.events.create(make_event().published(NOW), world.chapter.id, world.alice.id)


def test_cancelled_event_cannot_be_published_or_revised(world):
    ev = world.graph.events
    event = create_published(world)
    ev.cancel(event.id, world.alice.id)
    with pytest.raises(DomainRuleError):
        ev.revise(event.id, world.alice.id, NOW, title="Back on")
    with pytest.raises(DomainRuleError):
        ev.publish(event.id, world.alice.id, NOW)


def test_primary_or_duplicate_co_host_invites_are_rejected(world):
    ev = world.graph.events
    event = create_published(world)
    with pytest.raises(DomainRuleError):
        ev.invite_co_host(event.id, world.chapter.id, world.alice.id, NOW)
    ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)


def test_co_host_invite_must_target_an_existing_host(world):
    ev = world.graph.events
    event = create_published(world)
    for bad_id in ("missing", event.id):
        with pytest.raises(NotFoundError):
            ev.invite_co_host(event.id, bad_id, world.alice.id, NOW)


def test_only_invited_org_admin_can_accept(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    with pytest.raises(PermissionDeniedError):
        ev.accept_co_host(invite.id, world.alice.id)


def test_invited_org_can_decline_before_accepting(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.remove_co_host(invite.id, world.bob.id)
    assert ev.host_org_ids(event.id) == {world.chapter.id}


def test_primary_host_can_remove_co_host_but_outsiders_cannot(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    with pytest.raises(PermissionDeniedError):
        ev.remove_co_host(invite.id, world.carol.id)
    ev.remove_co_host(invite.id, world.alice.id)
    assert not ev.can_edit(world.bob.id, event.id)


def test_removed_co_host_can_be_reinvited(world):
    ev = world.graph.events
    event = create_published(world)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.remove_co_host(invite.id, world.bob.id)
    again = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    assert again.id != invite.id


def test_co_host_publishing_credits_the_primary_host(world):
    ev = world.graph.events
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    ev.publish(event.id, world.bob.id, NOW)
    store = world.graph.store
    assert store.node(world.chapter.id, Organization).last_event_published_at == NOW
    assert store.node(world.national.id, Organization).last_event_published_at is None


def test_event_tagging_follows_host_admin_rules(world):
    ev = world.graph.events
    event = create_published(world)
    tag = make_tag(world)
    with pytest.raises(PermissionDeniedError):
        ev.tag(event.id, tag.id, world.bob.id, NOW)
    assert ev.tag(event.id, tag.id, world.alice.id, NOW).source_id == event.id


def test_only_orgs_and_events_can_be_tagged_and_only_with_tags(world):
    ev = world.graph.events
    tag = make_tag(world)
    with pytest.raises(DomainRuleError):
        ev.tag(world.alice.id, tag.id, world.alice.id, NOW)
    with pytest.raises(NotFoundError):
        ev.tag(world.chapter.id, world.national.id, world.alice.id, NOW)


# --- series edge cases -------------------------------------------------------


def test_only_owner_admin_can_create_or_edit_series(world):
    s = world.graph.series
    with pytest.raises(PermissionDeniedError):
        s.create(
            EventSeries(
                owner_org_id=world.chapter.id, name="x", recurrence="weekly",
                default_title="x", default_duration=timedelta(hours=1),
                provenance=staff_provenance(),
            ),
            world.carol.id,
        )
    series = make_series(world)
    with pytest.raises(PermissionDeniedError):
        s.update_defaults(series.id, world.bob.id, NOW, location="Gym")


def test_series_rejects_unknown_default_fields(world):
    series = make_series(world)
    with pytest.raises(DomainRuleError):
        world.graph.series.update_defaults(series.id, world.alice.id, NOW, starts_at=NOW)


def test_event_not_hosted_by_series_owner_cannot_join(world):
    series = make_series(world)
    other = world.graph.events.create(make_event(), world.national.id, world.bob.id)
    with pytest.raises(PermissionDeniedError):
        world.graph.series.add_instance(other.id, series.id, world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        world.graph.series.add_instance(other.id, series.id, world.bob.id, NOW)


def test_series_edit_skips_cancelled_future_instances(world):
    s = world.graph.series
    series = make_series(world)
    (event,) = s.publish_occurrences(series.id, [NOW + timedelta(days=7)], world.alice.id, NOW)
    world.graph.events.cancel(event.id, world.alice.id)
    s.update_defaults(series.id, world.alice.id, NOW, location="Library")
    assert world.graph.store.node(event.id, Event).location == "Hall"


def test_series_edit_logs_location_change_on_published_instances(world):
    s = world.graph.series
    series = make_series(world)
    (event,) = s.publish_occurrences(series.id, [NOW + timedelta(days=7)], world.alice.id, NOW)
    s.update_defaults(series.id, world.alice.id, NOW, location="Library")
    log = world.graph.store.node(event.id, Event).change_log
    assert [(c.old_value, c.new_value) for c in log] == [("Hall", "Library")]


def test_publish_occurrences_rejects_past_start(world):
    series = make_series(world)
    starts = [NOW + timedelta(days=7), NOW - timedelta(days=1)]
    with pytest.raises(DomainRuleError):
        world.graph.series.publish_occurrences(series.id, starts, world.alice.id, NOW)


@pytest.mark.xfail(strict=True, reason="BUG: publish_occurrences is not wrapped in a "
                   "transaction, so earlier occurrences stay published")
def test_failed_publish_occurrences_leaves_nothing_behind(world):
    series = make_series(world)
    starts = [NOW + timedelta(days=7), NOW - timedelta(days=1)]
    with pytest.raises(DomainRuleError):
        world.graph.series.publish_occurrences(series.id, starts, world.alice.id, NOW)
    assert world.graph.series.instances(series.id) == []


# --- privacy edge cases ------------------------------------------------------


def test_network_visibility_requires_login(world):
    p = world.graph.privacy
    assert world.alice.visibility is Visibility.NETWORK
    assert p.can_view(world.carol.id, world.alice)
    assert not p.can_view(None, world.alice)


def test_private_membership_visible_only_to_its_owner(world):
    m, p = world.graph.membership, world.graph.privacy
    carol_edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(carol_edge.id, world.alice.id)
    m.set_visibility(world.alice_edge_id, world.alice.id, Visibility.PRIVATE)
    edge = world.graph.store.edge(world.alice_edge_id, MemberOf)
    assert p.can_view(world.alice.id, edge)
    assert not p.can_view(world.carol.id, edge)


def test_members_visibility_requires_confirmed_membership(world):
    m, p = world.graph.membership, world.graph.privacy
    edge = world.graph.store.edge(world.alice_edge_id, MemberOf)
    assert edge.visibility is Visibility.MEMBERS
    request = m.request(world.carol.id, world.chapter.id, NOW)
    assert not p.can_view(world.carol.id, edge)
    m.confirm(request.id, world.alice.id)
    assert p.can_view(world.carol.id, edge)


def test_connections_visibility_requires_an_established_connection(world):
    m, p, store = world.graph.membership, world.graph.privacy, world.graph.store
    m.set_visibility(world.alice_edge_id, world.alice.id, Visibility.CONNECTIONS)
    edge = store.edge(world.alice_edge_id, MemberOf)
    link = store.save_edge(
        Connected(source_id=world.alice.id, target_id=world.carol.id,
                  source_accepted=True, provenance=staff_provenance())
    )
    assert not p.can_view(world.carol.id, edge)
    store.save_edge(Connected(**{**vars(link), "target_accepted": True}))
    assert p.can_view(world.carol.id, edge)
    assert not p.can_view(world.bob.id, edge)


def test_draft_visible_to_co_host_admins_only_after_accepting(world):
    ev, p = world.graph.events, world.graph.privacy
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    assert not p.can_view(world.bob.id, event)
    ev.accept_co_host(invite.id, world.bob.id)
    assert p.can_view(world.bob.id, event)


@pytest.mark.xfail(strict=True, reason="BUG: cancelling a never-published draft makes it "
                   "public and discoverable")
def test_cancelled_draft_stays_hidden(world):
    ev, p = world.graph.events, world.graph.privacy
    event = ev.create(make_event(), world.chapter.id, world.alice.id)
    cancelled = ev.cancel(event.id, world.alice.id)
    assert cancelled.status is EventStatus.CANCELLED
    assert not p.can_view(None, cancelled)
    assert not cancelled.is_discoverable(NOW)


def test_erasure_keeps_org_claimed_when_another_admin_remains(world):
    m = world.graph.membership
    edge = m.request(world.carol.id, world.chapter.id, NOW)
    m.confirm(edge.id, world.alice.id)
    m.set_role(edge.id, MemberRole.ADMIN, world.alice.id)
    world.graph.privacy.erase_person(world.alice.id)
    assert world.graph.store.node(world.chapter.id, Organization).claim_state is ClaimState.CLAIMED
    assert m.is_admin(world.carol.id, world.chapter.id)


def test_erasure_releases_every_org_the_person_solely_administers(world):
    make_admin(world.graph, world.alice, world.council)
    world.graph.privacy.erase_person(world.alice.id)
    store = world.graph.store
    for org_id in (world.chapter.id, world.council.id):
        assert store.node(org_id, Organization).claim_state is ClaimState.UNCLAIMED


def test_erasure_also_deletes_ended_and_removed_edges(world):
    m, store = world.graph.membership, world.graph.store
    edge = m.request(world.alice.id, world.national.id, NOW)
    store.save_edge(edge.removed())
    world.graph.privacy.erase_person(world.alice.id)
    assert store.edges_touching(world.alice.id) == []
    assert store.edges(MemberOf, target_id=world.national.id, include_inactive=True) != []


@pytest.mark.xfail(strict=True, reason="BUG: release_if_last_admin ignores unconfirmed "
                   "claims, so the org is stuck in CLAIM_PENDING")
def test_erasing_a_pending_claimant_reopens_the_org(world):
    world.graph.membership.claim_org(world.carol.id, world.council.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    state = world.graph.store.node(world.council.id, Organization).claim_state
    assert state is ClaimState.UNCLAIMED
