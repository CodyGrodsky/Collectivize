"""Regression tests for bugs found in the graph services and storage.

Each test asserts the CORRECT behaviour and is marked xfail(strict=True)
while the bug is present, so the suite stays green and the test starts
failing loudly (XPASS) once the bug is fixed and the marker can be removed.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.config import Environment
from backend.graph.sqlite_store import SqliteGraphStore, read_environment_stamp
from backend.objects import (
    DomainRuleError,
    Event,
    EventSeries,
    GeoPoint,
    HostRole,
    Hosts,
    Organization,
    Person,
    RsvpStatus,
    Visibility,
)

from .conftest import NOW, make_event, staff_provenance



def make_series(world, **extra) -> EventSeries:
    series = EventSeries(
        owner_org_id=world.chapter.id,
        name="Weekly meeting",
        recurrence="Weekly",
        default_title="Chapter meeting",
        default_location="Hall",
        default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
        **extra,
    )
    return world.graph.series.create(series, world.alice.id)


def publish(world, event: Event) -> Event:
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


# --- series ------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "update_defaults saves the new series defaults before revising instances; "
                   "a rejected instance edit leaves the series changed (not atomic)")
def test_rejected_series_default_change_leaves_series_untouched(world):
    s = world.graph.series
    series = make_series(world)
    s.publish_occurrences(series.id, [NOW + timedelta(days=7)], world.alice.id, NOW)
    with pytest.raises(DomainRuleError):
        s.update_defaults(series.id, world.alice.id, NOW, capacity=0)
    stored = world.graph.store.node(series.id, EventSeries)
    assert stored.default_capacity is None  # actual: 0, an impossible default


# --- event lifecycle -----------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "EventService.create saves the event without checking its id is new, so "
                   "anyone can overwrite another host's event and add themselves as primary")
def test_create_cannot_overwrite_someone_elses_event(world):
    ev = world.graph.events
    original = publish(world, make_event("Alice's formal"))
    hijack = replace(make_event("Carol's party"), id=original.id)
    try:
        ev.create(hijack, world.carol.id, world.carol.id)
    except DomainRuleError:
        pass  # refusing is the correct outcome
    assert world.graph.store.node(original.id, Event).title == "Alice's formal"
    assert not ev.can_edit(world.carol.id, original.id)
    primaries = [
        h for h in world.graph.store.edges(Hosts, target_id=original.id)
        if h.role is HostRole.PRIMARY
    ]
    assert [h.source_id for h in primaries] == [world.chapter.id]


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "SeriesService.create saves without checking the id is new, so an admin "
                   "of one org can overwrite (and take over) another org's series")
def test_series_create_cannot_overwrite_another_orgs_series(world):
    theirs = make_series(world)  # owned by Alice's chapter
    takeover = EventSeries(
        id=theirs.id,
        owner_org_id=world.national.id,
        name="Mine now",
        recurrence="Weekly",
        default_title="Bob's meeting",
        default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
    )
    try:
        world.graph.series.create(takeover, world.bob.id)
    except DomainRuleError:
        pass  # refusing is the correct outcome
    assert world.graph.store.node(theirs.id, EventSeries).owner_org_id == world.chapter.id


# --- RSVPs -------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "clearing an RSVP hard-deletes it with its trip_recorded flag, so "
                   "going -> cleared -> going feeds the travel average twice for one event")
def test_one_trip_per_event_even_after_clearing_the_rsvp(world):
    here = GeoPoint(42.34, -71.09)
    event = publish(world, replace(make_event("Bonfire"), coordinates=GeoPoint(42.36, -71.09)))
    r = world.graph.rsvps
    r.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=here)
    r.respond(event.id, world.carol.id, None, NOW)
    r.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=here)
    assert world.graph.store.node(world.carol.id, Person).travel_samples == 1  # actual: 2


# --- privacy -----------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "PrivacyService._owner_ids returns no owners for an Organization, so a "
                   "PRIVATE org is invisible even to its own admins (roster.can_see lets them)")
def test_admins_can_see_their_own_private_org_and_its_calendar(world):
    store = world.graph.store
    store.save_node(store.node(world.chapter.id, Organization).with_visibility(Visibility.PRIVATE))
    org = store.node(world.chapter.id, Organization)
    assert world.graph.roster.can_see(world.alice.id, org.id)  # passes today
    assert world.graph.privacy.can_view(world.alice.id, org)  # fails today
    world.graph.calendar.host_events(org.id, world.alice.id)  # PermissionDeniedError today


# --- erasure -----------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "erase_person scrubs confirmed_by but leaves the erased id in "
                   "provenance.created_by of surviving records (e.g. co-host invitations)")
def test_erased_person_id_is_not_left_in_surviving_records(world):
    ev = world.graph.events
    event = publish(world, make_event("Joint mixer"))
    invite = ev.invite_co_host(event.id, world.national.id, world.alice.id, NOW)
    ev.accept_co_host(invite.id, world.bob.id)
    world.graph.privacy.erase_person(world.alice.id)
    survivor = world.graph.store.edge(invite.id, Hosts)
    assert survivor.provenance.created_by != world.alice.id


# --- sqlite store --------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: "
                   "read_environment_stamp builds a file: URI from the raw path, so a path "
                   "containing '#' (or '?') is misread and the stamp comes back as None")
def test_environment_stamp_is_read_from_paths_with_uri_characters(tmp_path):
    path = tmp_path / "week#1" / "graph.db"
    SqliteGraphStore(path, Environment.TEST).close()
    assert read_environment_stamp(path) == Environment.TEST.value
