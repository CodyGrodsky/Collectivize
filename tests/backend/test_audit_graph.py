"""Audit of the graph services: each test states the intended behaviour and
fails against the current code because of a real bug (see the docstrings)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.graph import PermissionDeniedError
from backend.objects import (
    DomainRuleError,
    Event,
    EventSeries,
    MemberOf,
    Organization,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Tag,
    Visibility,
)

from .conftest import NOW, make_event, staff_provenance


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


# --- calendar privacy -----------------------------------------------------------


def test_co_host_calendar_hides_event_whose_primary_host_viewer_cannot_see(world):
    """A co-host's calendar must not show an event whose primary host the viewer can't see."""
    g = world.graph
    event = g.events.create(make_event("Secret mixer"), world.chapter.id, world.alice.id)
    g.events.publish(event.id, world.alice.id, NOW)
    invite = g.events.invite_co_host(event.id, world.carol.id, world.alice.id, NOW)
    g.events.accept_co_host(invite.id, world.carol.id)
    chapter = g.store.node(world.chapter.id, Organization)
    g.store.save_node(chapter.with_visibility(Visibility.MEMBERS))
    stored = g.store.node(event.id, Event)
    # Bob isn't a chapter member: Discover/RSVP treat the event as missing...
    assert not g.privacy.can_view_listing(world.bob.id, stored)
    # ...but Carol's calendar still lists it to him.
    listing = g.calendar.host_events(world.carol.id, world.bob.id)
    assert [e.title for e in listing.public + listing.private] == []


# --- series ------------------------------------------------------------------------


def test_series_update_defaults_is_all_or_nothing(world):
    """A rejected series edit leaves the series and every instance unchanged."""
    s = world.graph.series
    series = make_series(world)
    first, second = s.publish_occurrences(
        series.id, [NOW + timedelta(days=1), NOW + timedelta(days=8)], world.alice.id, NOW
    )
    world.graph.events.revise(first.id, world.alice.id, NOW, capacity=5)  # override
    with pytest.raises(DomainRuleError):
        s.update_defaults(series.id, world.alice.id, NOW, location="Library", capacity=0)
    store = world.graph.store
    assert store.node(series.id, EventSeries).default_capacity is None
    assert store.node(series.id, EventSeries).default_location == "Hall"
    assert store.node(first.id, Event).location == "Hall"
    assert store.node(second.id, Event).location == "Hall"


# --- recommendation consent ---------------------------------------------------------


def test_membership_without_consent_does_not_feed_org_tags(world):
    """A membership marked recommendation_use=NONE never feeds the taste profile."""
    g = world.graph
    robotics = g.store.save_node(
        Tag(slug="robotics", label="Robotics", provenance=staff_provenance())
    )
    g.events.tag(world.chapter.id, robotics.id, world.alice.id, NOW)
    assert g.preferences.profile(world.alice.id, NOW).org_tags == {robotics.id}
    edge = g.store.edge(world.alice_edge_id, MemberOf)
    g.store.save_edge(edge.with_recommendation_use(RecommendationUse.NONE))
    assert g.preferences.profile(world.alice.id, NOW).org_tags == frozenset()


# --- timestamps ----------------------------------------------------------------------


def test_rsvp_rejects_naive_updated_at():
    """Rsvp.updated_at must be timezone-aware, like every other record timestamp."""
    with pytest.raises(DomainRuleError):
        Rsvp(
            source_id="person",
            target_id="event",
            status=RsvpStatus.GOING,
            updated_at=datetime(2026, 11, 13, 12, 0),  # naive
            provenance=staff_provenance(),
        )


# --- event ownership --------------------------------------------------------------------


def test_create_cannot_take_over_an_existing_event(world):
    """Creating an event with an existing event's id is refused (no second primary host)."""
    g = world.graph
    event = g.events.create(make_event("Alice's draft"), world.chapter.id, world.alice.id)
    with pytest.raises((DomainRuleError, PermissionDeniedError)):
        g.events.create(g.store.node(event.id, Event), world.carol.id, world.carol.id)
    assert not g.events.can_edit(world.carol.id, event.id)
    assert g.events.primary_host_id(event.id) == world.chapter.id


# --- storage round-trip ------------------------------------------------------------------


def test_stored_datetimes_keep_their_time_zone(store):
    """A datetime read back from any store keeps its zone, so DST-aware arithmetic matches."""
    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 7, 12, 0, tzinfo=tz)  # US DST starts 2026-03-08
    event = Event(
        title="Night before DST",
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        provenance=staff_provenance(),
    )
    store.save_node(event)
    loaded = store.node(event.id, Event)
    assert loaded.starts_at + timedelta(days=1) == event.starts_at + timedelta(days=1)
