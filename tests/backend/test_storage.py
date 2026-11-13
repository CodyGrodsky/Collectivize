from datetime import timedelta
from pathlib import Path

import pytest

from backend.config import (
    DEFAULT_PATHS,
    ConfigError,
    DatabaseConfig,
    Environment,
    load_config,
)
from backend.database import open_graph
from backend.graph import CollectivizeGraph, InMemoryGraphStore, NotFoundError
from backend.graph.codec import decode_record, encode_record
from backend.graph.sqlite_store import EnvironmentMismatchError, SqliteGraphStore
from backend.objects import (
    Edge,
    EventSeries,
    MemberOf,
    MemberRole,
    OrgKind,
    PersonOwnedEdge,
    RecommendationUse,
    Visibility,
)

from .conftest import NOW, make_event, make_org, make_person, staff_provenance

# --- config ------------------------------------------------------------------


def test_config_defaults_to_test_database():
    config = load_config({})
    assert config.environment is Environment.TEST
    assert config.path == DEFAULT_PATHS[Environment.TEST]


def test_config_selects_production_and_path_override():
    config = load_config({"COLLECTIVIZE_ENV": "Production", "COLLECTIVIZE_DB_PATH": "/x/p.db"})
    assert config.is_production and config.path == Path("/x/p.db")


def test_config_rejects_unknown_environment():
    with pytest.raises(ConfigError):
        load_config({"COLLECTIVIZE_ENV": "staging"})


def test_test_and_production_use_different_files():
    assert DEFAULT_PATHS[Environment.TEST] != DEFAULT_PATHS[Environment.PRODUCTION]


# --- codec -------------------------------------------------------------------


def test_codec_round_trips_nested_and_any_typed_values():
    event = make_event().published(NOW).revised(NOW, starts_at=NOW + timedelta(days=5),
                                                 ends_at=NOW + timedelta(days=5, hours=1))
    restored = decode_record("Event", encode_record(event))
    assert restored == event
    assert restored.change_log[0].old_value == event.change_log[0].old_value


def test_codec_round_trips_timedelta_and_enums():
    series = EventSeries(
        owner_org_id="o", name="s", recurrence="weekly", default_title="t",
        default_duration=timedelta(minutes=90), provenance=staff_provenance(),
    )
    assert decode_record("EventSeries", encode_record(series)) == series


def test_codec_rejects_unknown_types_and_fields():
    with pytest.raises(ValueError):
        decode_record("Nope", {})
    data = encode_record(make_event()) | {"injected": 1}
    with pytest.raises(ValueError):
        decode_record("Event", data)


# --- sqlite store ------------------------------------------------------------


def test_sqlite_persists_across_reopen(tmp_path):
    path = tmp_path / "test.db"
    graph = CollectivizeGraph(SqliteGraphStore(path, Environment.TEST))
    org = make_org(graph, "Club", OrgKind.CLUB)
    person = make_person(graph, "Pat")
    edge = graph.store.save_edge(
        MemberOf(source_id=person.id, target_id=org.id, role=MemberRole.ADMIN,
                 recommendation_use=RecommendationUse.NONE, provenance=staff_provenance())
    )
    graph.store.close()

    reopened = SqliteGraphStore(path, Environment.TEST)
    assert reopened.any_node(org.id) == org
    assert reopened.edge(edge.id, MemberOf) == edge
    assert reopened.edges(Edge, source_id=person.id) == [edge]
    assert reopened.edges(PersonOwnedEdge, target_id=org.id) == [edge]
    reopened.close()


def test_sqlite_filters_inactive_edges(tmp_path):
    store = SqliteGraphStore(tmp_path / "t.db", Environment.TEST)
    graph = CollectivizeGraph(store)
    org, person = make_org(graph, "Club", OrgKind.CLUB), make_person(graph, "Pat")
    edge = store.save_edge(MemberOf(source_id=person.id, target_id=org.id,
                                    provenance=staff_provenance()))
    store.save_edge(edge.removed())
    assert store.edges(MemberOf) == []
    assert len(store.edges(MemberOf, include_inactive=True)) == 1
    store.close()


def test_database_refuses_to_open_under_other_environment(tmp_path):
    path = tmp_path / "prod.db"
    SqliteGraphStore(path, Environment.PRODUCTION).close()
    with pytest.raises(EnvironmentMismatchError):
        SqliteGraphStore(path, Environment.TEST)


@pytest.mark.parametrize("make_store", [
    lambda tmp: InMemoryGraphStore(),
    lambda tmp: SqliteGraphStore(tmp / "t.db", Environment.TEST),
])
def test_transaction_rolls_back_on_error(tmp_path, make_store):
    store = make_store(tmp_path)
    graph = CollectivizeGraph(store)
    with pytest.raises(RuntimeError), store.transaction():
        make_org(graph, "Ghost", OrgKind.CLUB)
        raise RuntimeError("boom")
    kept = make_org(graph, "Kept", OrgKind.CLUB)
    assert store.has_node(kept.id)
    assert [e for e in store.edges(Edge)] == []
    store.close()


def test_open_graph_uses_configured_database(tmp_path):
    config = DatabaseConfig(Environment.TEST, tmp_path / "db" / "test.db")
    with open_graph(config) as graph:
        org = make_org(graph, "Club", OrgKind.CLUB)
    with open_graph(config) as graph:
        assert graph.store.any_node(org.id).visibility is Visibility.PUBLIC
    other = DatabaseConfig(Environment.TEST, tmp_path / "other.db")
    with pytest.raises(NotFoundError), open_graph(other) as graph:
        graph.store.any_node(org.id)
