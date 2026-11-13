import pytest

from backend.config import DatabaseConfig, Environment
from backend.database import open_graph
from backend.graph import CollectivizeGraph, InMemoryGraphStore
from backend.objects import ClaimState, Event, EventStatus, InstanceOf, Organization
from backend.seed import DEMO_PREFIX, ProductionSeedError, seed_mock_data

from .conftest import NOW


def test_seed_refuses_production():
    graph = CollectivizeGraph(InMemoryGraphStore())
    with pytest.raises(ProductionSeedError):
        seed_mock_data(graph, Environment.PRODUCTION, NOW)


def test_seeded_test_db_is_clearly_fictional_and_rule_abiding(tmp_path):
    config = DatabaseConfig(Environment.TEST, tmp_path / "test.db")
    with open_graph(config) as graph:
        result = seed_mock_data(graph, config.environment, NOW)

    with open_graph(config) as graph:
        store = graph.store
        orgs = {k: store.node(i, Organization) for k, i in result.orgs.items()}
        events = {k: store.node(i, Event) for k, i in result.events.items()}

        assert all(o.name.startswith(DEMO_PREFIX) for o in orgs.values())
        assert all(e.is_demo for e in events.values())
        assert orgs["chess"].claim_state is ClaimState.UNCLAIMED
        assert orgs["chapter"].claim_state is ClaimState.CLAIMED

        parents = {o.id for o in graph.hierarchy.parents(result.orgs["chapter"])}
        assert parents == {result.orgs["national"], result.orgs["ifc"]}
        assert len(graph.series.instances(_series_id(graph, result))) == 4
        assert graph.events.host_org_ids(result.events["fundraiser"]) == {
            result.orgs["chapter"], result.orgs["national"],
        }
        assert events["cancelled_mixer"].status is EventStatus.CANCELLED
        assert events["draft_formal"].status is EventStatus.DRAFT
        assert not graph.membership.is_member(result.people["emery"], result.orgs["robotics"])


def _series_id(graph: CollectivizeGraph, result) -> str:
    edge = graph.store.edges(InstanceOf, source_id=result.events["meeting_1"])[0]
    return edge.target_id
