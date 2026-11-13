from datetime import timedelta

import pytest

from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import (
    ClaimState,
    DomainRuleError,
    Event,
    EventSeries,
    MemberOf,
    Organization,
    Tag,
    Visibility,
)

from .conftest import NOW, make_event, make_person, staff_provenance


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
