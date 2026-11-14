import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anthropic
import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph, GraphStore, InMemoryGraphStore
from backend.graph.sqlite_store import SqliteGraphStore
from backend.objects import (
    Event,
    Organization,
    OrgKind,
    Person,
    Provenance,
    ProvenanceMethod,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
LIVE_AI = os.environ.get("COLLECTIVIZE_LIVE_AI") == "1"


class _NoNetworkClient:
    """Stands in for anthropic.Anthropic during tests: any attempt to create
    a real client fails like missing credentials, so code falls back and no
    request is ever sent (or paid for). Tests that need a client pass a fake.
    Live tests opt in with COLLECTIVIZE_LIVE_AI=1."""

    def __init__(self, *args, **kwargs):
        raise anthropic.AnthropicError("real API calls are disabled in tests")


@pytest.fixture(autouse=True)
def _block_real_api_calls(monkeypatch):
    if not LIVE_AI:
        monkeypatch.setattr(anthropic, "Anthropic", _NoNetworkClient)
STAFF = "staff-1"


def staff_provenance(at: datetime = NOW) -> Provenance:
    return Provenance(created_by=STAFF, method=ProvenanceMethod.STAFF, created_at=at)


def make_org(graph: CollectivizeGraph, name: str, kind: OrgKind) -> Organization:
    return graph.store.save_node(
        Organization(name=name, kind=kind, provenance=staff_provenance())
    )


def make_person(graph: CollectivizeGraph, name: str) -> Person:
    return graph.store.save_node(
        Person(
            display_name=name,
            provenance=Provenance(created_by=None, method=ProvenanceMethod.SELF_CLAIM),
        )
    )


def make_admin(graph: CollectivizeGraph, person: Person, org: Organization) -> str:
    """Run the full claim flow; returns the MemberOf edge id."""
    edge = graph.membership.claim_org(person.id, org.id, NOW)
    graph.membership.approve_claim(edge.id, STAFF, NOW)
    return edge.id


def make_event(title: str = "Chapter meeting", days_ahead: int = 3) -> Event:
    start = NOW + timedelta(days=days_ahead)
    return Event(
        title=title,
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        provenance=staff_provenance(),
    )


@dataclass
class World:
    graph: CollectivizeGraph
    chapter: Organization
    national: Organization
    council: Organization
    alice: Person  # chapter admin
    bob: Person  # national admin
    carol: Person  # no role
    alice_edge_id: str


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path) -> GraphStore:
    """Every service test runs against both storage backends."""
    if request.param == "memory":
        backend: GraphStore = InMemoryGraphStore()
    else:
        backend = SqliteGraphStore(tmp_path / "test.db", Environment.TEST)
    yield backend
    backend.close()


@pytest.fixture
def world(store: GraphStore) -> World:
    graph = CollectivizeGraph(store)
    chapter = make_org(graph, "Alpha Chapter", OrgKind.CHAPTER)
    national = make_org(graph, "Alpha National", OrgKind.NATIONAL)
    council = make_org(graph, "IFC", OrgKind.COUNCIL)
    alice = make_person(graph, "Alice")
    bob = make_person(graph, "Bob")
    carol = make_person(graph, "Carol")
    alice_edge_id = make_admin(graph, alice, chapter)
    make_admin(graph, bob, national)
    return World(graph, chapter, national, council, alice, bob, carol, alice_edge_id)
