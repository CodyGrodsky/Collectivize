"""Getting user data out: your own record, staff access, and the
consent-gated, pseudonymized dataset for analytics and machine learning."""

import json
from dataclasses import replace

import pytest

from backend.config import ConfigError
from backend.graph import PermissionDeniedError
from backend.graph.data_access import EXPORT_KEY_ENV, export_key_from_env
from backend.objects import (
    DataExport,
    DomainRuleError,
    GeoPoint,
    Person,
    RecommendationUse,
    RsvpStatus,
    Tag,
)

from .conftest import NOW, make_event, make_person, staff_provenance

KEY = b"a-test-key-that-is-long-enough"
PURPOSE = "Train the attendance model"


def consent(world, *people):
    for person in people:
        stored = world.graph.store.node(person.id, Person)
        world.graph.store.save_node(replace(stored, analytics_consent=True))


def connect(world, a, b):
    world.graph.connections.accept(world.graph.connections.request(a.id, b.id, NOW).id, b.id)


def publish(world, title="Meetup", **extra):
    event = replace(make_event(title), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def dataset(world, key=KEY):
    return world.graph.data_access.ml_dataset("staff-9", PURPOSE, NOW, key)


# --- your own data -------------------------------------------------------------


def test_you_can_get_everything_about_yourself(world):
    tag = world.graph.store.save_node(Tag(slug="chess", label="Chess", provenance=staff_provenance()))
    world.graph.preferences.add_interest(world.alice.id, tag.id, world.alice.id, NOW)
    connect(world, world.alice, world.bob)
    event = publish(world)
    world.graph.rsvps.respond(event.id, world.alice.id, RsvpStatus.GOING, NOW)
    world.graph.invites.create(world.alice.id, NOW)
    data = world.graph.data_access.person_data(world.alice.id, world.alice.id, NOW)
    assert data["person"]["display_name"] == "Alice"
    assert data["memberships"][0]["role"] == "admin"
    assert data["connections"] == [{"with": "Bob", "established": True, "origin": "request"}]
    assert data["rsvps"][0]["status"] == "going"
    assert data["interests"] == ["chess"]
    assert len(data["invites_sent"]) == 1 and "code" not in data["invites_sent"][0]
    assert data["hosted_events"] == []  # Alice hosts through her org, not personally
    assert data["learned"]["top_orgs"][0]["name"] == "Alpha Chapter"
    json.dumps(data)  # plain JSON-safe data


def test_nobody_else_can_get_your_data_without_a_staff_purpose(world):
    with pytest.raises(PermissionDeniedError):
        world.graph.data_access.person_data(world.alice.id, world.bob.id, NOW)


def test_staff_access_needs_a_real_purpose_and_is_recorded(world):
    with pytest.raises(DomainRuleError):
        world.graph.data_access.person_data(world.alice.id, "staff-1", NOW, staff_purpose="why")
    data = world.graph.data_access.person_data(
        world.alice.id, "staff-1", NOW, staff_purpose="Support ticket 1234: account issue"
    )
    assert data["person"]["display_name"] == "Alice"
    [record] = world.graph.store.nodes(DataExport)
    assert record.requested_by == "staff-1" and record.people == 1


def test_own_access_is_not_logged_as_an_export(world):
    world.graph.data_access.person_data(world.alice.id, world.alice.id, NOW)
    assert world.graph.store.nodes(DataExport) == []


# --- the ML dataset ------------------------------------------------------------


def test_only_people_who_opted_in_are_included(world):
    consent(world, world.alice)
    people = dataset(world).people
    assert len(people) == 1


def test_nobody_is_included_by_default(world):
    data = dataset(world)
    assert data.people == data.interactions == data.memberships == data.connections == []
    assert len(data.orgs) == 3  # public entities are always there


def test_people_are_pseudonymized_consistently(world):
    consent(world, world.alice, world.bob)
    first, again, other_key = dataset(world), dataset(world), dataset(world, b"x" * 32)
    ids = {p["person"] for p in first.people}
    assert ids == {p["person"] for p in again.people}
    assert ids.isdisjoint({p["person"] for p in other_key.people})
    assert ids.isdisjoint({world.alice.id, world.bob.id})


def test_no_names_emails_or_ids_of_people_leak(world):
    alice = world.graph.store.node(world.alice.id, Person)
    world.graph.store.save_node(replace(alice, email="alice@northeastern.edu"))
    consent(world, world.alice, world.bob)
    connect(world, world.alice, world.bob)
    event = publish(world, coordinates=GeoPoint(42.35, -71.1))
    world.graph.rsvps.respond(event.id, world.bob.id, RsvpStatus.GOING, NOW,
                              origin=GeoPoint(42.1234, -71.5678))
    dumped = json.dumps(dataset(world).tables())
    for secret in ("Alice", "Bob", "alice@", world.alice.id, world.bob.id, "42.1234", "71.5678"):
        assert secret not in dumped


def test_connections_need_both_people_to_consent(world):
    connect(world, world.alice, world.bob)
    connect(world, world.alice, world.carol)
    consent(world, world.alice, world.bob)
    assert len(dataset(world).connections) == 1


def test_facts_marked_not_for_recommendations_are_left_out(world):
    consent(world, world.carol)
    kept, hidden = publish(world, "Kept"), publish(world, "Hidden")
    world.graph.rsvps.respond(kept.id, world.carol.id, RsvpStatus.GOING, NOW)
    rsvp = world.graph.rsvps.respond(hidden.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.store.save_edge(rsvp.with_recommendation_use(RecommendationUse.NONE))
    assert [i["event_id"] for i in dataset(world).interactions] == [kept.id]


def test_rows_carry_useful_features(world):
    consent(world, world.alice)
    for _ in range(3):
        world.graph.preferences.record_trip(world.alice.id, 1.3)
    event = publish(world, coordinates=GeoPoint(42.35, -71.1))
    world.graph.rsvps.respond(event.id, world.alice.id, RsvpStatus.INTERESTED, NOW)
    data = dataset(world)
    [person] = data.people
    assert person["typical_trip_km"] == 1.5  # rounded to the nearest 0.5 km
    assert person["joined_month"] and person["verification"] == "none"
    [row] = data.interactions
    assert row["status"] == "interested" and row["person"] == person["person"]
    [event_row] = [e for e in data.events if e["event_id"] == event.id]
    assert event_row["host_org_ids"] == [world.chapter.id] and event_row["latitude"] == 42.35
    assert data.memberships[0]["role"] == "admin"


def test_person_hosts_appear_only_if_they_consented(world):
    event = make_event("Study group")
    world.graph.events.create(event, world.carol.id, world.carol.id)
    rows = {e["event_id"]: e for e in dataset(world).events}
    assert rows[event.id]["host_people"] == []
    consent(world, world.carol)
    rows = {e["event_id"]: e for e in dataset(world).events}
    assert len(rows[event.id]["host_people"]) == 1


def test_every_export_is_recorded(world):
    consent(world, world.alice)
    data = dataset(world)
    [record] = world.graph.store.nodes(DataExport)
    assert record.purpose == PURPOSE and record.requested_by == "staff-9"
    assert record.people == 1 and record.rows == sum(len(t) for t in data.tables().values())


def test_exports_need_a_purpose_and_a_strong_key(world):
    with pytest.raises(DomainRuleError):
        world.graph.data_access.ml_dataset("staff-9", "  ", NOW, KEY)
    with pytest.raises(ConfigError):
        world.graph.data_access.ml_dataset("staff-9", PURPOSE, NOW, b"short")


def test_the_key_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv(EXPORT_KEY_ENV, raising=False)
    with pytest.raises(ConfigError):
        export_key_from_env()
    monkeypatch.setenv(EXPORT_KEY_ENV, "too-short")
    with pytest.raises(ConfigError):
        export_key_from_env()
    monkeypatch.setenv(EXPORT_KEY_ENV, "k" * 32)
    assert export_key_from_env() == b"k" * 32


def test_erased_people_vanish_from_later_exports(world):
    consent(world, world.carol)
    world.graph.privacy.erase_person(world.carol.id)
    assert dataset(world).people == []


def test_new_people_start_without_analytics_consent(world):
    person = make_person(world.graph, "New")
    assert not person.analytics_consent and person.ai_uses_history
