from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    AttendanceMethod,
    Attended,
    Connected,
    DomainRuleError,
    Edge,
    EventSeries,
    HostRole,
    Hosts,
    MemberOf,
    MemberRole,
    Organization,
    OrgKind,
    PartOf,
    PartOfKind,
    Person,
    PersonOwnedEdge,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Visibility,
)

from .conftest import NOW, STAFF, make_event, make_org, make_person, staff_provenance

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


# --- config edge cases -------------------------------------------------------


def test_config_trims_values_and_ignores_blank_path_override():
    config = load_config({"COLLECTIVIZE_ENV": "  TEST \n", "COLLECTIVIZE_DB_PATH": "   "})
    assert config.environment is Environment.TEST
    assert config.path == DEFAULT_PATHS[Environment.TEST]


def test_config_reads_process_environment(monkeypatch):
    monkeypatch.setenv("COLLECTIVIZE_ENV", "production")
    monkeypatch.delenv("COLLECTIVIZE_DB_PATH", raising=False)
    assert load_config().path == DEFAULT_PATHS[Environment.PRODUCTION]


# --- codec edge cases --------------------------------------------------------


@pytest.mark.parametrize("edge", [
    PartOf(source_id="a", target_id="b", kind=PartOfKind.CHAPTER_OF, staff_verified=True,
           provenance=staff_provenance()),
    Hosts(source_id="a", target_id="b", role=HostRole.CO_HOST, provenance=staff_provenance()),
    Rsvp(source_id="a", target_id="b", status=RsvpStatus.GOING, updated_at=NOW,
         provenance=staff_provenance()),
    Attended(source_id="a", target_id="b", method=AttendanceMethod.TICKET_SCAN,
             recommendation_use=RecommendationUse.NETWORK, provenance=staff_provenance()),
    Connected(source_id="a", target_id="b", source_accepted=True, provenance=staff_provenance()),
], ids=lambda e: type(e).__name__)
def test_codec_round_trips_every_edge_type(edge):
    assert decode_record(type(edge).__name__, encode_record(edge)) == edge


def test_codec_round_trips_optional_and_tuple_fields():
    person = Person(display_name="Pat", embedding=(1.5, -2.0, 0.0), provenance=staff_provenance())
    org = Organization(
        name="Club", kind=OrgKind.CLUB, website_url="https://club.example",
        last_event_published_at=NOW, provenance=staff_provenance(),
    )
    for record in (person, org):
        assert decode_record(type(record).__name__, encode_record(record)) == record


def test_codec_preserves_timezone_offsets():
    tz = timezone(timedelta(hours=-5))
    start = datetime(2027, 1, 1, 18, 0, tzinfo=tz)
    event = replace(make_event(), starts_at=start, ends_at=start + timedelta(hours=1))
    restored = decode_record("Event", encode_record(event))
    assert restored.starts_at.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize("field, value", [
    ("title", "   "),
    ("starts_at", "2027-01-01T18:00:00"),  # naive
])
def test_decoding_reruns_domain_validation(field, value):
    data = encode_record(make_event()) | {field: value}
    with pytest.raises(DomainRuleError):
        decode_record("Event", data)


def test_codec_rejects_unknown_enum_values():
    data = encode_record(make_event()) | {"status": "bogus"}
    with pytest.raises(ValueError):
        decode_record("Event", data)


# --- store edge cases (both backends) ----------------------------------------


def test_store_rejects_edges_with_missing_endpoints(store):
    graph = CollectivizeGraph(store)
    org = make_org(graph, "Club", OrgKind.CLUB)
    with pytest.raises(NotFoundError):
        store.save_edge(MemberOf(source_id="ghost", target_id=org.id, provenance=staff_provenance()))
    assert store.edges(Edge) == []


def test_store_refuses_edges_as_nodes(store):
    with pytest.raises(TypeError):
        store.save_node(MemberOf(source_id="a", target_id="b", provenance=staff_provenance()))


def test_typed_lookup_of_wrong_or_missing_record_is_not_found(store):
    org = make_org(CollectivizeGraph(store), "Club", OrgKind.CLUB)
    with pytest.raises(NotFoundError):
        store.node(org.id, Person)
    with pytest.raises(NotFoundError):
        store.any_node("missing")
    with pytest.raises(NotFoundError):
        store.edge("missing", Edge)


def test_saving_again_replaces_the_latest_version(store):
    graph = CollectivizeGraph(store)
    org = make_org(graph, "Club", OrgKind.CLUB)
    person = make_person(graph, "Pat")
    edge = store.save_edge(MemberOf(source_id=person.id, target_id=org.id,
                                    provenance=staff_provenance()))
    store.save_node(replace(org, name="Renamed"))
    store.save_edge(edge.confirmed(by=STAFF))
    assert store.node(org.id, Organization).name == "Renamed"
    assert store.edges(MemberOf) == [edge.confirmed(by=STAFF)]


def test_hard_deletes_are_idempotent(store):
    org = make_org(CollectivizeGraph(store), "Club", OrgKind.CLUB)
    store.delete_node(org.id)
    store.delete_node(org.id)
    store.delete_edge("missing")
    assert not store.has_node(org.id)


def test_edge_queries_match_subclasses_only(store):
    graph = CollectivizeGraph(store)
    chapter = make_org(graph, "Chapter", OrgKind.CHAPTER)
    national = make_org(graph, "National", OrgKind.NATIONAL)
    person = make_person(graph, "Pat")
    member = store.save_edge(MemberOf(source_id=person.id, target_id=chapter.id,
                                      provenance=staff_provenance()))
    store.save_edge(PartOf(source_id=chapter.id, target_id=national.id,
                           kind=PartOfKind.CHAPTER_OF, provenance=staff_provenance()))
    assert store.edges(PersonOwnedEdge) == [member]
    assert len(store.edges(Edge)) == 2
    assert store.edges(Connected) == []


def test_outer_failure_rolls_back_nested_transactions(store):
    graph = CollectivizeGraph(store)
    with pytest.raises(RuntimeError), store.transaction():
        with store.transaction():
            inner = make_org(graph, "Inner", OrgKind.CLUB)
        raise RuntimeError("boom")
    assert not store.has_node(inner.id)


def test_caught_inner_failure_rolls_back_only_the_inner_block(store):
    graph = CollectivizeGraph(store)
    with store.transaction():
        outer = make_org(graph, "Outer", OrgKind.CLUB)
        with pytest.raises(RuntimeError), store.transaction():
            inner = make_org(graph, "Inner", OrgKind.CLUB)
            raise RuntimeError("boom")
    assert store.has_node(outer.id)
    assert not store.has_node(inner.id)


def test_three_levels_of_nesting_roll_back_independently(store):
    graph = CollectivizeGraph(store)
    with store.transaction():
        outer = make_org(graph, "Outer", OrgKind.CLUB)
        with store.transaction():
            middle = make_org(graph, "Middle", OrgKind.CLUB)
            with pytest.raises(RuntimeError), store.transaction():
                inner = make_org(graph, "Inner", OrgKind.CLUB)
                raise RuntimeError("boom")
            after = make_org(graph, "After", OrgKind.CLUB)
    assert all(store.has_node(o.id) for o in (outer, middle, after))
    assert not store.has_node(inner.id)


def test_sqlite_commits_nested_work_durably(tmp_path):
    path = tmp_path / "t.db"
    store = SqliteGraphStore(path, Environment.TEST)
    graph = CollectivizeGraph(store)
    with store.transaction(), store.transaction():
        org = make_org(graph, "Kept", OrgKind.CLUB)
    store.close()
    reopened = SqliteGraphStore(path, Environment.TEST)
    assert reopened.has_node(org.id)
    reopened.close()


# --- sqlite edge cases -------------------------------------------------------


def test_sqlite_supports_in_memory_databases():
    store = SqliteGraphStore(":memory:", Environment.TEST)
    org = make_org(CollectivizeGraph(store), "Club", OrgKind.CLUB)
    assert store.any_node(org.id) == org
    store.close()


def test_sqlite_round_trips_unicode_text(tmp_path):
    path = tmp_path / "t.db"
    store = SqliteGraphStore(path, Environment.TEST)
    org = make_org(CollectivizeGraph(store), "Café ☕ 日本語 \"quoted\" 'club'", OrgKind.CLUB)
    store.close()
    reopened = SqliteGraphStore(path, Environment.TEST)
    assert reopened.node(org.id, Organization).name == org.name
    reopened.close()


def test_test_database_refuses_production_and_keeps_its_data(tmp_path):
    path = tmp_path / "test.db"
    store = SqliteGraphStore(path, Environment.TEST)
    org = make_org(CollectivizeGraph(store), "Club", OrgKind.CLUB)
    store.close()
    with pytest.raises(EnvironmentMismatchError):
        SqliteGraphStore(path, Environment.PRODUCTION)
    reopened = SqliteGraphStore(path, Environment.TEST)
    assert reopened.has_node(org.id)
    reopened.close()
