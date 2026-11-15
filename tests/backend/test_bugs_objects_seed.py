"""Regression tests for bugs found in the domain objects and the seeders.

Each test asserts the CORRECT behaviour and is marked xfail(strict=True)
until the bug is fixed; once it is, the test will XPASS and fail the run,
so remove the marker then.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.objects import (
    Connected,
    DataExport,
    DomainRuleError,
    EventSeries,
    GraphRecord,
    InterestedIn,
    MemberOf,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    Rsvp,
    RsvpStatus,
    Tag,
    Visibility,
)
from backend.seed import seed_mock_data
from backend.seed_bulk import SMALL

from .conftest import NOW, staff_provenance


# --- object constructors ------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: Tag slug regex uses $, accepts a trailing newline")
def test_tag_slug_rejects_trailing_newline():
    with pytest.raises(DomainRuleError):
        Tag(slug="robotics\n", label="Robotics", provenance=staff_provenance())


@pytest.mark.xfail(strict=True, reason="BUG: Rsvp.updated_at is never checked for a time zone")
def test_rsvp_rejects_naive_updated_at():
    with pytest.raises(DomainRuleError):
        Rsvp(
            source_id="person", target_id="event", status=RsvpStatus.GOING,
            updated_at=datetime(2026, 11, 13, 12, 0),  # naive
            provenance=staff_provenance(),
        )


@pytest.mark.xfail(strict=True, reason="BUG: MemberOf accepts ended_at before started_at")
def test_membership_cannot_end_before_it_starts():
    with pytest.raises(DomainRuleError):
        MemberOf(
            source_id="person", target_id="org",
            started_at=NOW, ended_at=NOW - timedelta(days=30),
            provenance=staff_provenance(),
        )


@pytest.mark.xfail(strict=True, reason="BUG: DataExport audit record accepts negative counts")
def test_data_export_rejects_negative_counts():
    with pytest.raises(DomainRuleError):
        DataExport(
            requested_by="staff-1", purpose="Train the attendance model",
            people=-1, rows=-5, provenance=staff_provenance(),
        )


@pytest.mark.xfail(strict=True, reason="BUG: Person accepts rsvp_visibility outside "
                   "NETWORK/CONNECTIONS/PRIVATE (e.g. PUBLIC)")
def test_person_rejects_rsvp_visibility_that_is_not_a_choice():
    with pytest.raises(DomainRuleError):
        Person(
            display_name="Avery", rsvp_visibility=Visibility.PUBLIC,
            provenance=Provenance(created_by=None, method=ProvenanceMethod.SELF_CLAIM),
        )


def _series(**overrides) -> EventSeries:
    fields = dict(
        owner_org_id="org", name="Weekly meeting", recurrence="Weekly",
        default_title="Meeting", default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
    )
    return EventSeries(**{**fields, **overrides})


@pytest.mark.xfail(strict=True, reason="BUG: EventSeries accepts a non-positive default "
                   "capacity; every instantiate() then fails")
def test_event_series_rejects_non_positive_default_capacity():
    with pytest.raises(DomainRuleError):
        _series(default_capacity=0)


@pytest.mark.xfail(strict=True, reason="BUG: EventSeries accepts an empty name/default_title")
def test_event_series_rejects_blank_name_and_title():
    with pytest.raises(DomainRuleError):
        _series(name="   ", default_title="")


# --- seeders --------------------------------------------------------------------


def _by_name(graph: CollectivizeGraph) -> dict[str, str]:
    """Stable labels for random ids: a person's or org's name, an event's title."""
    labels = {}
    for node in graph.store.nodes(GraphRecord):
        labels[node.id] = (
            getattr(node, "display_name", None) or getattr(node, "title", None)
            or getattr(node, "name", None) or getattr(node, "slug", None)
        )
    return labels


def _demo_network_snapshot():
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)
    label = _by_name(graph)
    store = graph.store
    return {
        "rsvps": sorted(
            (label[e.source_id], label[e.target_id], e.status.value) for e in store.edges(Rsvp)
        ),
        "interests": sorted(
            (label[e.source_id], label[e.target_id]) for e in store.edges(InterestedIn)
        ),
        "travel": sorted((p.display_name, p.travel_samples) for p in store.nodes(Person)),
        "connections": sorted(
            tuple(sorted((label[e.source_id], label[e.target_id])))
            for e in store.edges(Connected)
        ),
    }


@pytest.mark.xfail(strict=True, reason="BUG: seed_network sorts by random uuid ids, so "
                   "RSVPs/interests differ every run despite RANDOM_SEED")
def test_hand_built_demo_network_is_the_same_every_time():
    first, second = _demo_network_snapshot(), _demo_network_snapshot()
    assert first["connections"] == second["connections"]  # already deterministic
    assert first["rsvps"] == second["rsvps"]
    assert first["interests"] == second["interests"]
    assert first["travel"] == second["travel"]


@pytest.fixture(scope="module")
def bulk_graph() -> CollectivizeGraph:
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW, SMALL)
    return graph


@pytest.mark.xfail(strict=True, reason="BUG: seed_bulk requests memberships before the "
                   "person's account was created")
def test_bulk_memberships_do_not_predate_the_account(bulk_graph):
    people = {p.id: p for p in bulk_graph.store.nodes(Person)}
    early = [
        m.id for m in bulk_graph.store.edges(MemberOf, include_inactive=True)
        if m.provenance.created_at < people[m.source_id].provenance.created_at
    ]
    assert early == []


@pytest.mark.xfail(strict=True, reason="BUG: seed_bulk orgs are created 'now' but claimed, "
                   "joined and publish events months earlier")
def test_bulk_orgs_are_not_claimed_before_they_exist(bulk_graph):
    store = bulk_graph.store
    early = []
    for org in store.nodes(Organization):
        for m in store.edges(MemberOf, target_id=org.id, include_inactive=True):
            if m.provenance.created_at < org.provenance.created_at:
                early.append(org.name)
                break
    assert early == []
