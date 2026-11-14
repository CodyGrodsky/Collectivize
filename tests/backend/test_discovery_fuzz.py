"""Fuzzing Discover: Hypothesis builds random worlds (orgs, people, events
with random places, times, tags, visibility, statuses, friendships, RSVPs,
interests and travel history) and random searches, then checks the
invariants every result page must keep.

Runs 120 random worlds per test by default. For a deeper hunt:
    FUZZ_EXAMPLES=2000 uv run --group dev pytest tests/backend/test_discovery_fuzz.py
"""

import math
import os
from dataclasses import replace
from datetime import timedelta

from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from backend.graph import CollectivizeGraph
from backend.graph.discovery import DiscoveryQuery
from backend.graph.store import InMemoryGraphStore
from backend.objects import (
    EventStatus,
    GeoPoint,
    OrgKind,
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

CAMPUS = GeoPoint(42.34, -71.09)
SLUGS = ("robotics", "social", "sports", "music", "food")

offsets = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False)
places = st.builds(
    lambda dlat, dlon: GeoPoint(CAMPUS.latitude + dlat, CAMPUS.longitude + dlon),
    offsets, offsets,
)
spots = st.one_of(places, places, places, st.none())  # mostly located
event_specs = st.fixed_dictionaries({
    "host": st.integers(0, 2),
    "days": st.floats(min_value=0.01, max_value=60, allow_nan=False),
    "spot": spots,
    "tags": st.sets(st.integers(0, len(SLUGS) - 1), max_size=3),
    "visibility": st.sampled_from(
        [Visibility.PUBLIC] * 3 + [Visibility.NETWORK, Visibility.MEMBERS]
    ),
    "fate": st.sampled_from(["published"] * 6 + ["cancelled", "draft"]),
    "capacity": st.none() | st.integers(1, 5),
})
worlds = st.fixed_dictionaries({
    "events": st.lists(event_specs, min_size=3, max_size=12),
    "people": st.integers(2, 6),
    "friendships": st.lists(st.tuples(st.integers(0, 5), st.integers(0, 5)),
                            min_size=3, max_size=12),
    "memberships": st.lists(st.tuples(st.integers(0, 5), st.integers(0, 2)), max_size=8),
    "rsvps": st.lists(
        st.tuples(st.integers(0, 5), st.integers(0, 11),
                  st.sampled_from([RsvpStatus.GOING] * 3 + list(RsvpStatus))),
        min_size=5, max_size=30,
    ),
    "interests": st.lists(st.tuples(st.integers(0, 5), st.integers(0, len(SLUGS) - 1)),
                          max_size=8),
    "trips": st.lists(st.tuples(st.integers(0, 5),
                                st.floats(min_value=0, max_value=600, allow_nan=False)),
                      max_size=15),
    "hidden_rsvps": st.sets(st.integers(0, 5), max_size=3),
})
searches = st.fixed_dictionaries({
    "viewer": st.sampled_from([None, 0, 1, 2, 3, 4, 5]),  # usually logged in
    "origin": st.one_of(st.just(CAMPUS), places, st.just(CAMPUS), st.none()),
    "radius": st.one_of(st.none(), st.none(),
                        st.floats(min_value=0.01, max_value=200, allow_nan=False)),
    "include_unlocated": st.booleans(),
    "include_cancelled": st.booleans(),
    "tag_filter": st.one_of(st.just(set()), st.just(set()),
                            st.sets(st.integers(0, len(SLUGS) - 1), max_size=2)),
    "preferred": st.sets(st.integers(0, len(SLUGS) - 1), max_size=2),
    "limit": st.integers(1, 7),
})


def build(spec):
    graph = CollectivizeGraph(InMemoryGraphStore())
    tags = [graph.store.save_node(Tag(slug=s, label=s.title(), provenance=staff_provenance()))
            for s in SLUGS]
    orgs, admins = [], []
    for i in range(3):
        org = make_org(graph, f"Org {i}", OrgKind.CLUB)
        admin = make_person(graph, f"Admin {i}")
        make_admin(graph, admin, org)
        orgs.append(org)
        admins.append(admin)
    people = [make_person(graph, f"Person {i}") for i in range(spec["people"])]
    events = [_add_event(graph, e, orgs, admins, tags) for e in spec["events"]]
    _connect(graph, spec, people, orgs, admins, events, tags)
    return graph, people, tags


def _add_event(graph, e, orgs, admins, tags):
    host, admin = orgs[e["host"]], admins[e["host"]]
    event = replace(make_event("Event", days_ahead=1), coordinates=e["spot"],
                    visibility=e["visibility"], capacity=e["capacity"],
                    starts_at=NOW + timedelta(days=e["days"]),
                    ends_at=NOW + timedelta(days=e["days"], hours=2))
    graph.events.create(event, host.id, admin.id)
    for t in e["tags"]:
        graph.events.tag(event.id, tags[t].id, admin.id, NOW)
    if e["fate"] != "draft":
        graph.events.publish(event.id, admin.id, NOW)
    if e["fate"] == "cancelled":
        graph.events.cancel(event.id, admin.id)
    return event


def _connect(graph, spec, people, orgs, admins, events, tags):
    pick = lambda i: people[i % len(people)]
    for a, b in spec["friendships"]:
        x, y = pick(a), pick(b)
        if x.id != y.id and graph.connections.between(x.id, y.id) is None:
            graph.connections.accept(graph.connections.request(x.id, y.id, NOW).id, y.id)
    for p, o in spec["memberships"]:
        person = pick(p)
        if graph.membership.current(person.id, orgs[o].id) is None:
            edge = graph.membership.request(person.id, orgs[o].id, NOW)
            graph.membership.confirm(edge.id, admins[o].id)
    for p in spec["hidden_rsvps"]:
        graph.rsvps.set_rsvp_visibility(pick(p).id, pick(p).id, Visibility.PRIVATE)
    for p, e, status in spec["rsvps"]:
        try:
            graph.rsvps.respond(events[e % len(events)].id, pick(p).id, status, NOW)
        except (LookupError, ValueError):
            pass  # can't see it, not open, or full: the rules said no
    for p, t in spec["interests"]:
        graph.preferences.add_interest(pick(p).id, tags[t].id, pick(p).id, NOW)
    for p, km in spec["trips"]:
        graph.preferences.record_trip(pick(p).id, km)


def query_for(search, people, tags, **extra):
    viewer = None if search["viewer"] is None else people[search["viewer"] % len(people)].id
    origin = search["origin"]
    radius = search["radius"] if origin is not None else None
    return DiscoveryQuery(
        viewer_id=viewer, origin=origin, max_distance_km=radius,
        include_unlocated=search["include_unlocated"],
        include_cancelled=search["include_cancelled"],
        tag_ids=tuple(tags[t].id for t in search["tag_filter"]),
        preferred_tag_ids=tuple(tags[t].id for t in search["preferred"]),
        **extra,
    )


FUZZ = settings(max_examples=int(os.environ.get("FUZZ_EXAMPLES", "120")), deadline=None,
                suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


@FUZZ
@given(worlds, searches)
def test_every_page_keeps_its_promises(world_spec, search):
    graph, people, tags = build(world_spec)
    query = query_for(search, people, tags, limit=100)
    page = graph.discovery.search(query, NOW)
    items = page.items
    assert page.total == len(items)
    event(f"results: {min(len(items), 4)}{'+' if len(items) >= 4 else ''}")
    event(f"with friends going: {any(i.friends_going for i in items)}")
    event(f"with distances: {any(i.distance_km is not None for i in items)}")

    for item in items:
        # A real probability, explained.
        assert 0.0 <= item.score <= 1.0 and math.isfinite(item.score)
        assert all(math.isfinite(f.effect) for f in item.factors)
        assert item.reasons, "every result explains itself"
        # Only what the viewer may see.
        assert graph.privacy.can_view(query.viewer_id, item.event)
        assert item.event.status is not EventStatus.DRAFT
        # Filters hold.
        if not query.include_cancelled:
            assert not item.is_cancelled
        if item.is_cancelled:
            assert item.score == 0.0 and item.reasons[0].startswith("Cancelled")
        if query.max_distance_km is not None:
            if item.distance_km is None:
                assert query.include_unlocated
            else:
                assert item.distance_km <= query.max_distance_km
        if query.tag_ids:
            assert any(t in query.tag_ids for t in graph.discovery._tag_ids(
                item.event.id, graph.events.host_org_ids(item.event.id)))
        # Friends going really are friends who are going and visible.
        if query.viewer_id is None:
            assert item.friends_going == () and item.my_rsvp is None
        friends = set(graph.connections.connections_of(query.viewer_id or ""))
        for friend in item.friends_going:
            assert friend.id in friends
            assert graph.rsvps.mine(item.event.id, friend.id).status is RsvpStatus.GOING
        if item.going_count is not None:
            assert item.going_count >= 5

    # Ordering: live events by likelihood (high first), cancelled last.
    keys = [(i.is_cancelled, -i.score) for i in items]
    assert keys == sorted(keys)
    # No event appears twice.
    assert len({i.event.id for i in items}) == len(items)


@FUZZ
@given(worlds, searches)
def test_pages_split_the_full_list_exactly(world_spec, search):
    graph, people, tags = build(world_spec)
    full = graph.discovery.search(query_for(search, people, tags, limit=100), NOW)
    limit = search["limit"]
    seen, offset = [], 0
    while True:
        page = graph.discovery.search(
            query_for(search, people, tags, limit=limit, offset=offset), NOW
        )
        assert page.total == full.total
        seen.extend(i.event.id for i in page.items)
        if not page.has_more:
            break
        offset += limit
    assert seen == [i.event.id for i in full.items]


@FUZZ
@given(worlds, searches)
def test_searching_is_read_only_and_repeatable(world_spec, search):
    graph, people, tags = build(world_spec)
    snapshot = (dict(graph.store._nodes), dict(graph.store._edges))
    query = query_for(search, people, tags, limit=100)
    first = graph.discovery.search(query, NOW)
    second = graph.discovery.search(query, NOW)
    assert [(i.event.id, i.score) for i in first.items] == [
        (i.event.id, i.score) for i in second.items
    ]
    assert (graph.store._nodes, graph.store._edges) == snapshot
    assert graph.integrity.audit() == ()
