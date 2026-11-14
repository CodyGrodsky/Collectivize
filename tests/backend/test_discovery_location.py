"""Location, travel habits and tastes in Discover."""

import math
from dataclasses import replace

import pytest

from backend.graph.discovery import DiscoveryQuery
from backend.graph.explain import format_distance
from backend.graph.preferences import MIN_TRAVEL_SAMPLES
from backend.objects import DomainRuleError, GeoPoint, Person, RsvpStatus, Tag

from .conftest import NOW, make_event, make_person, staff_provenance

CAMPUS = GeoPoint(42.3400, -71.0900)
KM_PER_DEGREE = 111.195


def north(km):
    return GeoPoint(CAMPUS.latitude + km / KM_PER_DEGREE, CAMPUS.longitude)


def publish(world, title, km=None, days=3, **extra):
    event = replace(make_event(title, days_ahead=days),
                    coordinates=None if km is None else north(km), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def search(world, viewer=None, **filters):
    viewer_id = viewer.id if viewer else None
    return world.graph.discovery.search(DiscoveryQuery(viewer_id=viewer_id, **filters), NOW)


def titles(page):
    return [i.event.title for i in page.items]


def by_title(page):
    return {i.event.title: i for i in page.items}


# --- radius filter -------------------------------------------------------------


def test_radius_keeps_only_nearby_events(world):
    publish(world, "Here", km=0.2)
    publish(world, "Edge", km=4.9)
    publish(world, "Far", km=25)
    publish(world, "Nowhere")
    page = search(world, origin=CAMPUS, max_distance_km=5)
    assert set(titles(page)) == {"Here", "Edge", "Nowhere"}
    page = search(world, origin=CAMPUS, max_distance_km=5, include_unlocated=False)
    assert set(titles(page)) == {"Here", "Edge"}


def test_radius_boundary_is_inclusive(world):
    event = publish(world, "Exactly", km=3)
    exact = CAMPUS.distance_km(event.coordinates)
    assert titles(search(world, origin=CAMPUS, max_distance_km=exact)) == ["Exactly"]
    assert titles(search(world, origin=CAMPUS, max_distance_km=exact * 0.999)) == []


@pytest.mark.parametrize("bad", [
    {"max_distance_km": 5},  # no origin
    {"origin": CAMPUS, "max_distance_km": 0},
    {"origin": CAMPUS, "max_distance_km": -1},
    {"origin": CAMPUS, "max_distance_km": math.nan},
    {"origin": CAMPUS, "max_distance_km": math.inf},
    {"origin": CAMPUS, "max_distance_km": True},
])
def test_invalid_radius_queries_are_rejected(bad):
    with pytest.raises(DomainRuleError):
        DiscoveryQuery(**bad)


def test_every_item_reports_its_distance(world):
    publish(world, "Located", km=1.0)
    publish(world, "Unlocated")
    items = by_title(search(world, origin=CAMPUS))
    assert items["Located"].distance_km == pytest.approx(1.0, abs=0.01)
    assert items["Unlocated"].distance_km is None
    assert all(i.distance_km is None for i in search(world).items)  # no origin given


# --- distance in the ranking ---------------------------------------------------


def test_nearer_ranks_higher_when_otherwise_equal(world):
    publish(world, "Far", km=8)
    publish(world, "Near", km=0.3)
    publish(world, "Middle", km=2)
    assert titles(search(world, origin=CAMPUS)) == ["Near", "Middle", "Far"]


def test_no_position_means_distance_plays_no_part(world):
    publish(world, "Far", km=8, days=3)
    publish(world, "Near", km=0.3, days=3)
    scores = {i.event.title: i.score for i in search(world).items}
    assert scores["Far"] == pytest.approx(scores["Near"])


def test_unlocated_events_sit_between_near_and_far(world):
    publish(world, "Near", km=0.2)
    publish(world, "Unknown")
    publish(world, "Far", km=30)
    assert titles(search(world, origin=CAMPUS)) == ["Near", "Unknown", "Far"]


def test_your_usual_distance_shapes_what_counts_as_near(world):
    publish(world, "Ten km away", km=10)
    publish(world, "Unlocated")
    homebody, traveller = world.carol, make_person(world.graph, "Traveller")
    for _ in range(MIN_TRAVEL_SAMPLES):
        world.graph.preferences.record_trip(homebody.id, 0.5)
        world.graph.preferences.record_trip(traveller.id, 15.0)
    assert titles(search(world, homebody, origin=CAMPUS)) == ["Unlocated", "Ten km away"]
    assert titles(search(world, traveller, origin=CAMPUS)) == ["Ten km away", "Unlocated"]


def test_a_radius_sets_the_scale_before_you_have_history(world):
    publish(world, "Six km", km=6)
    wide = by_title(search(world, origin=CAMPUS, max_distance_km=50))["Six km"]
    narrow = by_title(search(world, origin=CAMPUS, max_distance_km=7))["Six km"]
    assert wide.score > narrow.score


def test_distance_reasons(world):
    publish(world, "Next door", km=0.1)
    publish(world, "Out of town", km=40)
    items = by_title(search(world, origin=CAMPUS))
    assert "Close to you (100 m away)" in items["Next door"].reasons
    assert "Farther than you usually go (40 km away)" in items["Out of town"].reasons


@pytest.mark.parametrize(("km", "text"), [
    (0.0, "under 50 m"), (0.049, "under 50 m"), (0.05, "50 m"), (0.43, "450 m"),
    (0.999, "1000 m"), (1.0, "1.0 km"),
    (9.94, "9.9 km"), (12.6, "13 km"), (20015.0, "20015 km"),
])
def test_distance_formatting(km, text):
    assert format_distance(km) == text


# --- trips are learned from RSVPs ----------------------------------------------


def trip_count(world, person):
    return world.graph.store.node(person.id, Person).travel_samples


def test_going_with_a_position_records_a_trip(world):
    event = publish(world, "Meetup", km=2)
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=CAMPUS)
    assert trip_count(world, world.carol) == 1


def test_trips_are_recorded_once_per_event_and_only_for_going(world):
    event = publish(world, "Meetup", km=2)
    rsvps = world.graph.rsvps
    rsvps.respond(event.id, world.carol.id, RsvpStatus.INTERESTED, NOW, origin=CAMPUS)
    assert trip_count(world, world.carol) == 0
    rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=CAMPUS)
    rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=CAMPUS)
    assert trip_count(world, world.carol) == 1


def test_no_trip_without_a_position_or_a_location(world):
    located, unlocated = publish(world, "A", km=2), publish(world, "B")
    world.graph.rsvps.respond(located.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.rsvps.respond(unlocated.id, world.carol.id, RsvpStatus.GOING, NOW, origin=CAMPUS)
    assert trip_count(world, world.carol) == 0


def test_a_full_event_records_no_trip(world):
    event = publish(world, "Tiny", km=2, capacity=1)
    world.graph.rsvps.respond(event.id, world.alice.id, RsvpStatus.GOING, NOW)
    with pytest.raises(DomainRuleError):
        world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW, origin=CAMPUS)
    assert trip_count(world, world.carol) == 0


# --- tastes in the ranking -----------------------------------------------------


def make_tag(world, slug):
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


def tagged(world, title, *tags, days=3):
    event = publish(world, title, days=days)
    for t in tags:
        world.graph.events.tag(event.id, t.id, world.alice.id, NOW)
    return event


def test_chosen_interests_lift_matching_events_and_say_so(world):
    robotics, cooking = make_tag(world, "robotics"), make_tag(world, "cooking")
    world.graph.preferences.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    tagged(world, "Bake-off", cooking, days=1)
    tagged(world, "Robot build", robotics, days=6)
    page = search(world, world.carol)
    assert titles(page)[0] == "Robot build"
    assert "Matches your interest: Robotics" in page.items[0].reasons


def test_closer_match_to_your_tastes_ranks_higher(world):
    robotics, ai, cooking = (make_tag(world, s) for s in ("robotics", "ai", "cooking"))
    for t in (robotics, ai):
        world.graph.preferences.add_interest(world.carol.id, t.id, world.carol.id, NOW)
    tagged(world, "Both", robotics, ai)
    tagged(world, "One", robotics)
    tagged(world, "Diluted", robotics, cooking)
    tagged(world, "None", cooking)
    scores = {i.event.title: i.score for i in search(world, world.carol).items}
    assert scores["Both"] > scores["One"] > scores["Diluted"] > scores["None"]


def test_categories_you_go_to_lift_similar_events(world):
    robotics, cooking = make_tag(world, "robotics"), make_tag(world, "cooking")
    past = tagged(world, "Last build night", robotics, days=1)
    world.graph.rsvps.respond(past.id, world.carol.id, RsvpStatus.GOING, NOW)
    tagged(world, "Next build night", robotics, days=8)
    tagged(world, "Cooking class", cooking, days=8)
    items = by_title(search(world, world.carol))
    assert items["Next build night"].score > items["Cooking class"].score
    assert "Like events you've gone to: Robotics" in items["Next build night"].reasons


def test_reasons_follow_the_model(world):
    """Every listed reason after the host line corresponds to a factor that
    actually moved the estimate."""
    robotics = make_tag(world, "robotics")
    world.graph.preferences.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    event = tagged(world, "Robot build", robotics)
    item = by_title(search(world, world.carol, origin=north(0.05)))["Robot build"]
    effects = {f.name: f.effect for f in item.factors}
    assert effects["affinity"] > 0 and "Matches your interest: Robotics" in item.reasons
    assert event.coordinates is None and item.distance_km is None
    assert not any("away" in r for r in item.reasons)
