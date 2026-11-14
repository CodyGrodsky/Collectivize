"""Edge cases for Discover, the ranking model, reasons, preferences and RSVPs.

Tests marked xfail(strict=True) assert the correct behaviour for a known bug.
"""

import math
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.graph import CollectivizeGraph, NotFoundError
from backend.graph.discovery import DiscoveryQuery, RequestMatch
from backend.graph.explain import NOTABLE_EFFECT, explain
from backend.graph.preferences import EMPTY_PROFILE, MIN_TRAVEL_SAMPLES, TasteProfile
from backend.graph.ranking import EventSignals, estimate
from backend.graph.store import InMemoryGraphStore
from backend.objects import (
    DomainRuleError,
    GeoPoint,
    Organization,
    OrgKind,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Tag,
    Visibility,
)

from .conftest import (
    NOW,
    make_admin,
    make_event,
    make_org,
    make_person,
    staff_provenance,
)

GOING, INTERESTED, NOT_GOING = RsvpStatus.GOING, RsvpStatus.INTERESTED, RsvpStatus.NOT_GOING
CAMPUS = GeoPoint(42.3400, -71.0900)
KM_PER_DEGREE = 111.195


def north(km):
    return GeoPoint(CAMPUS.latitude + km / KM_PER_DEGREE, CAMPUS.longitude)


def publish(world, title="Bonfire", days=3, km=None, **extra):
    event = replace(
        make_event(title, days_ahead=days),
        coordinates=None if km is None else north(km),
        **extra,
    )
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def search(world, viewer=None, now=NOW, **filters):
    viewer_id = viewer.id if viewer else None
    return world.graph.discovery.search(DiscoveryQuery(viewer_id=viewer_id, **filters), now)


def titles(page):
    return [i.event.title for i in page.items]


def by_title(page):
    return {i.event.title: i for i in page.items}


def connect(world, a, b):
    request = world.graph.connections.request(a.id, b.id, NOW)
    world.graph.connections.accept(request.id, b.id)


def make_tag(world, slug):
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


def trips(world, person):
    return world.graph.store.node(person.id, type(person)).travel_samples


# --- time boundaries -----------------------------------------------------------


def test_event_under_way_is_still_listed_and_maximally_soon(world):
    event = publish(world, "Long fair", days=1)
    during = event.starts_at + timedelta(minutes=30)
    item = search(world, now=during).items[0]
    soon = next(c for c in item.factors if c.name == "soonness")
    assert soon.value == pytest.approx(1.0)


def test_event_ending_exactly_now_is_gone_and_frozen(world):
    event = publish(world)
    assert titles(search(world, now=event.ends_at)) == []
    assert titles(search(world, now=event.ends_at - timedelta(microseconds=1))) == ["Bonfire"]
    with pytest.raises(DomainRuleError, match="ended"):
        world.graph.rsvps.respond(event.id, world.carol.id, GOING, event.ends_at)
    just_before = event.ends_at - timedelta(microseconds=1)
    assert world.graph.rsvps.respond(event.id, world.carol.id, GOING, just_before)


def test_event_under_way_is_not_described_as_starting_soon(world):
    event = publish(world, "Weekend retreat", days=1)
    event = world.graph.events.revise(
        event.id, world.alice.id, NOW, ends_at=event.starts_at + timedelta(days=3)
    )
    during = event.starts_at + timedelta(days=1)
    reasons = search(world, now=during).items[0].reasons
    assert "Starts within 48 hours" not in reasons


def test_non_utc_now_gives_the_same_ranking_as_utc(world):
    publish(world, "A", days=1)
    publish(world, "B", days=5)
    tokyo = NOW.astimezone(timezone(timedelta(hours=9)))
    utc_page, tokyo_page = search(world), search(world, now=tokyo)
    assert [(i.event.id, i.score) for i in utc_page.items] == [
        (i.event.id, i.score) for i in tokyo_page.items
    ]


def test_non_utc_event_times_survive_storage_and_window_filter(world):
    plus5 = timezone(timedelta(hours=5))
    start = (NOW + timedelta(days=2)).astimezone(plus5)
    publish(world, "Offset", days=2, starts_at=start, ends_at=start + timedelta(hours=1))
    page = search(world, starts_after=NOW + timedelta(days=2), starts_before=NOW + timedelta(days=3))
    assert titles(page) == ["Offset"]
    days = next(c for c in page.items[0].factors if c.name == "soonness")
    assert days.value == pytest.approx(math.exp(-2 / 7))


def test_window_is_inclusive_at_start_and_exclusive_at_end(world):
    event = publish(world, "Edge", days=2)
    assert titles(search(world, starts_after=event.starts_at)) == ["Edge"]
    assert titles(search(world, starts_before=event.starts_at)) == []


def test_naive_window_datetimes_are_rejected_up_front():
    with pytest.raises(DomainRuleError):
        DiscoveryQuery(starts_after=datetime(2026, 11, 14, 12, 0))


# --- pagination and determinism ------------------------------------------------


def test_offset_beyond_total_gives_an_empty_last_page(world):
    publish(world, "Only")
    page = search(world, offset=50, limit=10)
    assert page.items == () and page.total == 1 and not page.has_more
    edge = search(world, offset=1, limit=1)
    assert edge.items == () and not edge.has_more


def test_exact_ties_are_ordered_by_id_regardless_of_insert_order(world):
    a = publish(world, "Twin")
    b = publish(world, "Twin")
    page = search(world)
    assert page.items[0].score == page.items[1].score
    assert [i.event.id for i in page.items] == sorted([a.id, b.id])
    assert [i.event.id for i in search(world).items] == [i.event.id for i in page.items]


# --- text, tags, empty inputs --------------------------------------------------


def test_casefolded_text_matching_handles_sharp_s(world):
    publish(world, "Straße fest")
    assert titles(search(world, text="STRASSE")) == ["Straße fest"]


def test_decomposed_accents_still_match(world):
    publish(world, "Café night")
    query = unicodedata.normalize("NFD", "café")
    assert titles(search(world, text=query)) == ["Café night"]


def test_whitespace_only_text_matches_everything(world):
    publish(world, "Anything")
    assert titles(search(world, text="   \t ")) == ["Anything"]


def test_event_with_no_tags_has_zero_affinity_and_no_taste_reasons(world):
    robotics = make_tag(world, "robotics")
    world.graph.preferences.add_interest(world.carol.id, robotics.id, world.carol.id, NOW)
    publish(world, "Plain")
    item = search(world, world.carol).items[0]
    assert next(c for c in item.factors if c.name == "affinity").value == 0.0
    assert not any("interest" in r or "Like " in r for r in item.reasons)


def test_unknown_preferred_tag_is_harmless(world):
    publish(world, "Plain")
    page = search(world, world.carol, preferred_tag_ids=("no-such-tag",))
    assert titles(page) == ["Plain"]


def test_tag_filter_with_no_matching_tag_returns_nothing(world):
    publish(world, "Plain")
    assert titles(search(world, tag_ids=("no-such-tag",))) == []


# --- RSVP state transitions and capacity --------------------------------------


def test_going_not_going_going_keeps_one_edge(world):
    event = publish(world)
    rsvps = world.graph.rsvps
    first = rsvps.respond(event.id, world.carol.id, GOING, NOW)
    rsvps.respond(event.id, world.carol.id, NOT_GOING, NOW)
    again = rsvps.respond(event.id, world.carol.id, GOING, NOW)
    assert again.id == first.id
    assert len(world.graph.store.edges(Rsvp, target_id=event.id)) == 1


def test_releasing_a_place_lets_someone_else_take_it_and_blocks_your_return(world):
    event = publish(world, capacity=1)
    rsvps = world.graph.rsvps
    rsvps.respond(event.id, world.carol.id, GOING, NOW)
    rsvps.respond(event.id, world.carol.id, INTERESTED, NOW)
    rsvps.respond(event.id, world.bob.id, GOING, NOW)
    with pytest.raises(DomainRuleError, match="full"):
        rsvps.respond(event.id, world.carol.id, GOING, NOW)


def test_capacity_lowered_below_going_keeps_existing_and_blocks_new(world):
    event = publish(world, capacity=3)
    rsvps = world.graph.rsvps
    rsvps.respond(event.id, world.carol.id, GOING, NOW)
    rsvps.respond(event.id, world.bob.id, GOING, NOW)
    world.graph.events.revise(event.id, world.alice.id, NOW, capacity=1)
    assert rsvps.respond(event.id, world.carol.id, GOING, NOW).status is GOING
    with pytest.raises(DomainRuleError, match="full"):
        rsvps.respond(event.id, world.alice.id, GOING, NOW)
    assert rsvps.respond(event.id, world.alice.id, NOT_GOING, NOW).status is NOT_GOING


def test_clearing_a_missing_rsvp_is_a_no_op(world):
    event = publish(world)
    assert world.graph.rsvps.respond(event.id, world.carol.id, None, NOW) is None


def test_rsvp_to_unknown_event_looks_missing(world):
    with pytest.raises(NotFoundError):
        world.graph.rsvps.respond("no-such-event", world.carol.id, GOING, NOW)


def test_archived_event_takes_no_rsvps(world):
    event = publish(world)
    world.graph.events.archive(event.id, world.alice.id)
    with pytest.raises(NotFoundError):
        world.graph.rsvps.respond(event.id, world.carol.id, GOING, NOW)


def test_cannot_rsvp_to_an_event_hidden_by_its_host(world):
    event = publish(world, "Secret society social")
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    assert titles(search(world, world.carol)) == []
    with pytest.raises(NotFoundError):
        world.graph.rsvps.respond(event.id, world.carol.id, GOING, NOW)


# --- travel averaging ----------------------------------------------------------


def test_flip_flopping_going_records_one_trip(world):
    event = publish(world, "Meetup", km=2)
    rsvps = world.graph.rsvps
    rsvps.respond(event.id, world.carol.id, GOING, NOW, origin=CAMPUS)
    rsvps.respond(event.id, world.carol.id, NOT_GOING, NOW, origin=CAMPUS)
    rsvps.respond(event.id, world.carol.id, GOING, NOW, origin=CAMPUS)
    assert trips(world, world.carol) == 1


def test_trip_at_exactly_the_max_is_kept_and_just_beyond_ignored(world):
    prefs = world.graph.preferences
    prefs.record_trip(world.carol.id, 500.0)
    prefs.record_trip(world.carol.id, 500.0000001)
    assert trips(world, world.carol) == 1


def test_identical_trips_give_that_distance_back(world):
    for _ in range(MIN_TRAVEL_SAMPLES + 5):
        world.graph.preferences.record_trip(world.carol.id, 3.0)
    profile = world.graph.preferences.profile(world.carol.id, NOW)
    assert profile.typical_trip_km == pytest.approx(3.0)


# --- learned categories --------------------------------------------------------


def test_future_dated_rsvps_never_count_more_than_fresh_ones(world):
    robotics, cooking = make_tag(world, "robotics"), make_tag(world, "cooking")
    a, b = publish(world, "A"), publish(world, "B")
    world.graph.events.tag(a.id, robotics.id, world.alice.id, NOW)
    world.graph.events.tag(b.id, cooking.id, world.alice.id, NOW)
    world.graph.rsvps.respond(a.id, world.carol.id, GOING, NOW)
    world.graph.rsvps.respond(b.id, world.carol.id, GOING, NOW + timedelta(days=1))
    learned = world.graph.preferences.profile(world.carol.id, NOW).learned
    assert learned == {robotics.id: 1.0, cooking.id: 1.0}


def test_rsvp_to_a_vanished_event_is_ignored_not_fatal():
    graph = CollectivizeGraph(InMemoryGraphStore())
    org = make_org(graph, "Club", OrgKind.CHAPTER)
    admin, guest = make_person(graph, "Admin"), make_person(graph, "Guest")
    make_admin(graph, admin, org)
    event = graph.events.create(make_event("Gone"), org.id, admin.id)
    graph.events.publish(event.id, admin.id, NOW)
    graph.rsvps.respond(event.id, guest.id, GOING, NOW)
    graph.store.delete_node(event.id)  # stale data: the RSVP edge outlives it
    assert graph.preferences.profile(guest.id, NOW).learned == {}
    assert graph.activity.recent_events(guest.id) == []


# --- privacy of friends going --------------------------------------------------


def test_no_consent_rsvps_do_not_feed_a_friends_ranking(world):
    event = publish(world)
    connect(world, world.carol, world.alice)
    saved = world.graph.rsvps.respond(event.id, world.alice.id, GOING, NOW)
    world.graph.store.save_edge(saved.with_recommendation_use(RecommendationUse.NONE))
    item = search(world, world.carol).items[0]
    assert item.friends_going == ()
    assert next(c for c in item.factors if c.name == "friends").value == 0.0


def test_rsvp_to_members_only_event_is_hidden_from_non_member_friends(world):
    event = publish(world, "Members night", visibility=Visibility.MEMBERS)
    connect(world, world.carol, world.alice)
    saved = world.graph.rsvps.respond(event.id, world.alice.id, GOING, NOW)
    assert not world.graph.privacy.can_view(world.carol.id, event)
    assert not world.graph.privacy.can_view(world.carol.id, saved)
    assert world.graph.rsvps.friends_going(world.carol.id, event.id) == ()


def test_pending_connection_is_not_a_friend_going(world):
    event = publish(world)
    world.graph.connections.request(world.carol.id, world.alice.id, NOW)
    world.graph.rsvps.respond(event.id, world.alice.id, GOING, NOW)
    assert world.graph.rsvps.friends_going(world.carol.id, event.id) == ()


def test_network_rsvp_from_a_private_person_is_not_shown(world):
    event = publish(world)
    connect(world, world.carol, world.alice)
    world.graph.rsvps.set_rsvp_visibility(world.alice.id, world.alice.id, Visibility.NETWORK)
    private = world.graph.store.node(world.alice.id, type(world.alice)).with_visibility(
        Visibility.PRIVATE
    )
    world.graph.store.save_node(private)
    world.graph.rsvps.respond(event.id, world.alice.id, GOING, NOW)
    assert world.graph.rsvps.friends_going(world.carol.id, event.id) == ()


def test_logged_out_sees_counts_from_exactly_the_threshold(world):
    event = publish(world)
    for i in range(5):
        world.graph.rsvps.respond(event.id, make_person(world.graph, f"G{i}").id, GOING, NOW)
    item = search(world).items[0]
    assert item.going_count == 5 and item.friends_going == () and item.my_rsvp is None


# --- request matches -----------------------------------------------------------


@pytest.mark.parametrize("bad", [math.nan, -0.01, 1.01, math.inf])
def test_impossible_request_scores_are_rejected(bad):
    with pytest.raises(DomainRuleError):
        RequestMatch(score=bad)


def test_request_match_for_unknown_event_is_ignored(world):
    publish(world, "A")
    page = world.graph.discovery.search(
        DiscoveryQuery(), NOW, request_matches={"nope": RequestMatch(1.0, "x")}
    )
    assert titles(page) == ["A"]


# --- reasons agree with the model ---------------------------------------------

signals_strategy = st.builds(
    EventSignals,
    friends_going=st.integers(0, 50),
    my_org_hosts=st.booleans(),
    affinity=st.floats(0, 1),
    distance_km=st.one_of(st.none(), st.floats(0, 200)),
    travel_scale_km=st.floats(0.25, 50),
    days_until=st.floats(-3, 60),
    host_trust=st.sampled_from([0.0, 0.4, 1.0]),
    host_inactive_days=st.one_of(st.none(), st.floats(0, 400)),
    request_match=st.one_of(st.none(), st.floats(0, 1)),
)


@settings(max_examples=150, deadline=None)
@given(signals=signals_strategy)
def test_distance_and_request_reasons_match_their_effects(signals):
    host = Organization(name="Org", kind=OrgKind.CHAPTER, provenance=staff_provenance())
    result = estimate(signals)
    reasons = explain(signals, result, host, [], set(), EMPTY_PROFILE, frozenset(), {})
    close = any(r.startswith("Close to you") for r in reasons)
    far = any(r.startswith("Farther than you usually go") for r in reasons)
    proximity = result.effect("proximity")
    assert close == (signals.distance_km is not None and proximity >= NOTABLE_EFFECT)
    assert far == (signals.distance_km is not None and proximity <= -NOTABLE_EFFECT)
    fits = any(r.startswith("Fits what you asked for") for r in reasons)
    assert fits == (result.effect("request_match") >= NOTABLE_EFFECT)
    assert 0.0 <= result.likelihood <= 1.0


def test_learned_reason_needs_a_strong_enough_category():
    weak = TasteProfile(learned={"t": 0.2})
    signals = EventSignals(affinity=0.5)
    result = estimate(signals)
    host = Organization(name="Org", kind=OrgKind.CHAPTER, provenance=staff_provenance())
    reasons = explain(signals, result, host, [], {"t"}, weak, frozenset(), {"t": "T"})
    assert not any("gone to" in r for r in reasons)


def test_repeated_searches_give_identical_scores_and_order(world):
    publish(world, "Near", km=0.5, days=1)
    publish(world, "Far", km=20, days=10)
    first = [(i.event.title, i.score) for i in search(world, origin=CAMPUS).items]
    second = [(i.event.title, i.score) for i in search(world, origin=CAMPUS).items]
    assert first == second
    assert [t for t, _ in first] == ["Near", "Far"]
