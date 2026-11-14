"""Interests, learned categories, and the private travel average."""

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.graph import CollectivizeGraph, PermissionDeniedError
from backend.graph.codec import encode_record
from backend.graph.preferences import (
    CATEGORY_HALF_LIFE,
    MAX_TRIP_KM,
    MIN_TRAVEL_SAMPLES,
    PreferenceService,
    decay,
    typical_trip_km,
)
from backend.graph.store import InMemoryGraphStore
from backend.objects import (
    DomainRuleError,
    GeoPoint,
    InterestedIn,
    Person,
    Provenance,
    ProvenanceMethod,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Tag,
)

from .conftest import NOW, make_event, make_person, staff_provenance

CAMPUS = GeoPoint(42.3400, -71.0900)


def tag(world, slug):
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


def located_event(world, title="Meetup", km_north=0.0, days=3, tags=()):
    lat = CAMPUS.latitude + km_north / 111.195  # ~111 km per degree of latitude
    event = replace(make_event(title, days_ahead=days), coordinates=GeoPoint(lat, CAMPUS.longitude))
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    for t in tags:
        world.graph.events.tag(event.id, t.id, world.alice.id, NOW)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


# --- interests -----------------------------------------------------------------


def test_only_you_set_your_interests(world):
    robotics = tag(world, "robotics")
    prefs = world.graph.preferences
    first = prefs.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    assert prefs.add_interest(world.carol.id, robotics.id, world.carol.id, NOW).id == first.id
    assert [t.slug for t in prefs.interests_of(world.carol.id, world.carol.id)] == ["robotics"]
    for action in (
        lambda: prefs.add_interest(world.carol.id, robotics.id, world.alice.id, NOW),
        lambda: prefs.remove_interest(world.carol.id, robotics.id, world.alice.id),
        lambda: prefs.interests_of(world.carol.id, world.alice.id),
        lambda: prefs.my_profile(world.carol.id, world.alice.id, NOW),
        lambda: prefs.forget_travel(world.carol.id, world.alice.id),
    ):
        with pytest.raises(PermissionDeniedError):
            action()
    prefs.remove_interest(world.carol.id, robotics.id, world.carol.id)
    assert prefs.interests_of(world.carol.id, world.carol.id) == []


def test_interests_must_be_real_tags(world):
    with pytest.raises(LookupError):
        world.graph.preferences.add_interest(world.carol.id, world.chapter.id, world.carol.id, NOW)


def test_interests_with_no_consent_are_not_used(world):
    robotics = tag(world, "robotics")
    edge = world.graph.preferences.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    assert world.graph.preferences.profile(world.carol.id, NOW).chosen == {robotics.id}
    world.graph.store.save_edge(edge.with_recommendation_use(RecommendationUse.NONE))
    assert world.graph.preferences.profile(world.carol.id, NOW).chosen == frozenset()


def test_logged_out_profile_is_empty(world):
    profile = world.graph.preferences.profile(None, NOW)
    assert profile.vector() == {}
    assert profile.typical_trip_km is None


# --- learned categories --------------------------------------------------------


def test_categories_are_learned_from_your_rsvps(world):
    robotics, social = tag(world, "robotics"), tag(world, "social")
    a = located_event(world, "Build night", tags=(robotics,))
    b = located_event(world, "Mixer", tags=(social,))
    world.graph.rsvps.respond(a.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.rsvps.respond(b.id, world.carol.id, RsvpStatus.INTERESTED, NOW)
    learned = world.graph.preferences.profile(world.carol.id, NOW).learned
    assert learned == {robotics.id: 1.0, social.id: pytest.approx(0.5)}


def test_not_going_and_no_consent_rsvps_teach_nothing(world):
    robotics = tag(world, "robotics")
    event = located_event(world, tags=(robotics,))
    rsvp = world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.NOT_GOING, NOW)
    assert world.graph.preferences.profile(world.carol.id, NOW).learned == {}
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.store.save_edge(
        world.graph.store.edge(rsvp.id, Rsvp).with_recommendation_use(RecommendationUse.NONE)
    )
    assert world.graph.preferences.profile(world.carol.id, NOW).learned == {}


def test_older_rsvps_fade(world):
    robotics, social = tag(world, "robotics"), tag(world, "social")
    old = located_event(world, "Old", tags=(robotics,))
    new = located_event(world, "New", tags=(social,))
    world.graph.rsvps.respond(old.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.rsvps.respond(new.id, world.carol.id, RsvpStatus.GOING, NOW)
    later = NOW + CATEGORY_HALF_LIFE
    # Re-stamp the "new" RSVP as if made one half-life after the old one.
    edge = world.graph.rsvps.mine(new.id, world.carol.id)
    world.graph.store.save_edge(replace(edge, updated_at=later))
    learned = world.graph.preferences.profile(world.carol.id, later).learned
    assert learned[social.id] == 1.0
    assert learned[robotics.id] == pytest.approx(0.5)


def test_learned_categories_include_tags_inherited_from_hosts(world):
    robotics = tag(world, "robotics")
    world.graph.events.tag(world.chapter.id, robotics.id, world.alice.id, NOW)
    event = located_event(world)
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    assert world.graph.preferences.profile(world.carol.id, NOW).learned == {robotics.id: 1.0}


@given(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False))
def test_decay_is_between_zero_and_one(seconds):
    value = decay(timedelta(seconds=seconds), CATEGORY_HALF_LIFE)
    assert 0.0 <= value <= 1.0


def test_decay_reference_values():
    assert decay(timedelta(0), CATEGORY_HALF_LIFE) == 1.0
    assert decay(-CATEGORY_HALF_LIFE, CATEGORY_HALF_LIFE) == 1.0  # future: full weight
    assert decay(CATEGORY_HALF_LIFE * 2, CATEGORY_HALF_LIFE) == pytest.approx(0.25)


# --- travel --------------------------------------------------------------------


def trips(prefs, person, distances):
    for d in distances:
        prefs.record_trip(person.id, d)
    return prefs._store.node(person.id, Person)


def test_no_typical_distance_until_enough_trips(world):
    prefs = world.graph.preferences
    person = trips(prefs, world.carol, [2.0] * (MIN_TRAVEL_SAMPLES - 1))
    assert typical_trip_km(person) is None
    person = trips(prefs, world.carol, [2.0])
    assert typical_trip_km(person) == pytest.approx(2.0)


def test_typical_distance_is_robust_to_one_long_trip(world):
    person = trips(world.graph.preferences, world.carol, [1.0, 1.0, 1.0, 1.0, 200.0])
    assert 1.0 < typical_trip_km(person) < 10.0  # one long trip doesn't swamp it


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, MAX_TRIP_KM + 1])
def test_impossible_or_extreme_trips_are_ignored(world, bad):
    person = trips(world.graph.preferences, world.carol, [bad])
    assert person.travel_samples == 0
    assert person.travel_log_km is None


def test_zero_distance_trips_are_fine(world):
    person = trips(world.graph.preferences, world.carol, [0.0] * MIN_TRAVEL_SAMPLES)
    assert typical_trip_km(person) == pytest.approx(0.0, abs=1e-9)


@given(st.lists(st.floats(min_value=0, max_value=MAX_TRIP_KM), min_size=1, max_size=60))
@settings(max_examples=150)
def test_typical_distance_stays_within_the_trips_taken(distances):
    graph = CollectivizeGraph(InMemoryGraphStore())
    person = make_person(graph, "Pat")
    person = trips(PreferenceService(graph.store), person, distances)
    assert person.travel_samples == len(distances)
    if len(distances) >= MIN_TRAVEL_SAMPLES:
        typical = typical_trip_km(person)
        assert min(distances) - 1e-6 <= typical <= max(distances) + 1e-6


def test_forget_travel_resets_everything(world):
    trips(world.graph.preferences, world.carol, [3.0] * 5)
    person = world.graph.preferences.forget_travel(world.carol.id, world.carol.id)
    assert person.travel_samples == 0
    assert person.travel_log_km is None


def test_corrupt_travel_average_cannot_overflow():
    person = Person(display_name="X", travel_log_km=1e6, travel_samples=10,
                    provenance=Provenance(created_by=None, method=ProvenanceMethod.SELF_CLAIM))
    assert typical_trip_km(person) == pytest.approx(MAX_TRIP_KM)


@pytest.mark.parametrize("fields", [
    {"travel_samples": -1},
    {"travel_samples": 2},  # a count with no average
    {"travel_log_km": 1.0},  # an average with no count
    {"travel_log_km": math.nan, "travel_samples": 1},
])
def test_person_rejects_inconsistent_travel_data(fields):
    with pytest.raises(DomainRuleError):
        Person(display_name="X", provenance=staff_provenance(), **fields)


# --- what is (and isn't) stored ------------------------------------------------


def test_positions_and_single_distances_are_never_stored(world):
    event = located_event(world, km_north=1.5)
    here = GeoPoint(42.123456, -71.654321)
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=here)
    dump = json.dumps(
        [encode_record(n) for n in world.graph.store.nodes(Person)]
        + [encode_record(e) for e in world.graph.store.edges(Rsvp)]
    )
    assert "42.123456" not in dump and "71.654321" not in dump
    rsvp = world.graph.rsvps.mine(event.id, world.carol.id)
    assert not any("distance" in name for name in vars(rsvp))


def test_erasing_an_account_removes_interests_and_travel(world):
    robotics = tag(world, "robotics")
    world.graph.preferences.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    trips(world.graph.preferences, world.carol, [2.0] * 3)
    world.graph.privacy.erase_person(world.carol.id)
    assert world.graph.store.edges(InterestedIn, include_inactive=True) == []
    assert not world.graph.store.has_node(world.carol.id)
