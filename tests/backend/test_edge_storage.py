"""Edge cases for storage, the codec, configuration, seeding, the ML export
and the command-line scripts.

Tests marked xfail(strict=True) assert the CORRECT behaviour and document a
real bug; they start failing (XPASS) once the bug is fixed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.config import (
    DB_PATH_VAR,
    ENV_VAR,
    ConfigError,
    DatabaseConfig,
    Environment,
    load_config,
)
from backend.database import open_graph
from backend.graph import CollectivizeGraph, InMemoryGraphStore, NotFoundError
from backend.graph import sqlite_store as sqlite_store_module
from backend.graph.codec import decode_record, encode_record, record_types
from backend.graph.data_access import (
    EXPORT_KEY_ENV,
    MIN_KEY_BYTES,
    _pseudonym,
    export_key_from_env,
)
from backend.graph.sqlite_store import EnvironmentMismatchError, SqliteGraphStore
from backend.objects import (
    Connected,
    DataExport,
    DomainRuleError,
    Event,
    EventSeries,
    GeoPoint,
    MemberOf,
    Organization,
    OrgKind,
    Person,
    Provenance,
    ProvenanceMethod,
    RsvpStatus,
    Tag,
    Visibility,
)
from backend.seed import ProductionSeedError, seed_mock_data

from .conftest import NOW, make_event, make_person, staff_provenance

ROOT = Path(__file__).resolve().parents[2]
KEY = b"0123456789abcdef"  # exactly MIN_KEY_BYTES
PURPOSE = "Train the attendance model"
FAST = settings(max_examples=40, deadline=None,
                suppress_health_check=[HealthCheck.function_scoped_fixture])


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_edge_script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def roundtrip(record):
    return decode_record(type(record).__name__, json.loads(json.dumps(encode_record(record))))


def sqlite_roundtrip(record, tmp_path: Path):
    store = SqliteGraphStore(tmp_path / "rt.db", Environment.TEST)
    try:
        store.save_node(record)
        return store.any_node(record.id)
    finally:
        store.close()


@pytest.fixture
def db_env(monkeypatch, tmp_path):
    """Point configuration at a temporary test database."""
    path = tmp_path / "db" / "test.db"
    monkeypatch.setenv(ENV_VAR, "test")
    monkeypatch.setenv(DB_PATH_VAR, str(path))
    return path


# --- codec ---------------------------------------------------------------------

text = st.text(min_size=1).filter(lambda s: s.strip())


@FAST
@given(name=text, profile=st.text(), email=st.none() | st.text())
def test_person_unicode_text_round_trips_through_json(name, profile, email):
    person = Person(display_name=name, profile=profile, email=email, provenance=staff_provenance())
    assert roundtrip(person) == person


@FAST
@given(
    lat=st.floats(min_value=-90, max_value=90, allow_nan=False),
    lon=st.floats(min_value=-180, max_value=180, allow_nan=False),
    capacity=st.none() | st.integers(min_value=1, max_value=10**30),
)
def test_event_with_nested_geopoint_and_huge_capacity_round_trips(lat, lon, capacity):
    event = replace(make_event(), coordinates=GeoPoint(lat, lon), capacity=capacity)
    assert roundtrip(event) == event


def test_huge_integers_survive_sqlite(tmp_path):
    event = replace(make_event(), capacity=10**30)
    assert sqlite_roundtrip(event, tmp_path).capacity == 10**30


def test_extreme_float_embedding_round_trips():
    values = (5e-324, 1.7976931348623157e308, -0.0, 1e-300)
    person = Person(display_name="P", embedding=values, provenance=staff_provenance())
    restored = roundtrip(person)
    assert restored.embedding == values
    assert math.copysign(1, restored.embedding[2]) == -1


def test_none_in_optional_nested_field_round_trips():
    event = replace(make_event(), coordinates=None, capacity=None, embedding=None)
    restored = roundtrip(event)
    assert restored.coordinates is None and restored.embedding is None


def test_non_utc_offsets_keep_their_instant_and_offset():
    tz = timezone(timedelta(hours=5, minutes=45))
    start = datetime(2027, 3, 1, 9, 30, 15, 123456, tzinfo=tz)
    event = replace(make_event(), starts_at=start, ends_at=start + timedelta(microseconds=1))
    restored = roundtrip(event)
    assert restored.starts_at == start and restored.starts_at.utcoffset() == tz.utcoffset(None)
    assert restored.ends_at - restored.starts_at == timedelta(microseconds=1)


@FAST
@given(td=st.timedeltas(min_value=timedelta(microseconds=1), max_value=timedelta(days=3650)))
def test_realistic_timedeltas_round_trip_exactly(td):
    series = EventSeries(owner_org_id="o", name="s", recurrence="r", default_title="t",
                         default_duration=td, provenance=staff_provenance())
    assert roundtrip(series).default_duration == td


def test_naive_datetime_in_stored_data_is_rejected():
    data = encode_record(Person(display_name="P", provenance=staff_provenance()))
    data["provenance"]["created_at"] = "2026-01-01T00:00:00"
    with pytest.raises(DomainRuleError):
        decode_record("Person", data)


def test_old_records_missing_new_defaulted_fields_still_load():
    data = encode_record(Person(display_name="P", provenance=staff_provenance()))
    for new_field in ("analytics_consent", "ai_uses_history", "travel_log_km", "travel_samples"):
        del data[new_field]
    person = decode_record("Person", data)
    assert person.analytics_consent is False and person.ai_uses_history is True


def test_extra_fields_from_another_version_are_rejected_not_silently_dropped():
    data = encode_record(Person(display_name="P", provenance=staff_provenance())) | {"x": 1}
    with pytest.raises(ValueError):
        decode_record("Person", data)


def test_record_missing_a_required_field_raises_value_error():
    data = encode_record(Person(display_name="P", provenance=staff_provenance()))
    del data["display_name"]
    with pytest.raises(ValueError):
        decode_record("Person", data)


def test_every_record_class_is_registered_for_decoding():
    from backend import objects
    from backend.objects.base import GraphRecord

    defined = {
        cls.__name__ for cls in vars(objects).values()
        if isinstance(cls, type) and issubclass(cls, GraphRecord)
    }
    assert defined <= set(record_types())


def test_logged_location_change_round_trips(tmp_path):
    event = make_event().published(NOW).revised(NOW, location="Hall ✓")
    assert sqlite_roundtrip(event, tmp_path) == event


def test_logged_coordinate_change_can_be_saved_to_sqlite(tmp_path):
    event = replace(make_event(), coordinates=GeoPoint(1, 2)).published(NOW)
    revised = event.revised(NOW, coordinates=GeoPoint(3, 4))
    assert sqlite_roundtrip(revised, tmp_path) == revised


def test_revising_coordinates_through_the_service_works_on_sqlite(tmp_path):
    graph = CollectivizeGraph(SqliteGraphStore(tmp_path / "t.db", Environment.TEST))
    try:
        host = make_person(graph, "Host")
        event = replace(make_event(), coordinates=GeoPoint(1, 2))
        graph.events.create(event, host.id, host.id)
        graph.events.publish(event.id, host.id, NOW)
        graph.events.revise(event.id, host.id, NOW, coordinates=GeoPoint(3, 4))
        assert graph.store.node(event.id, Event).coordinates == GeoPoint(3, 4)
    finally:
        graph.store.close()


# --- stores: in-memory vs sqlite --------------------------------------------------


def test_node_order_is_insertion_order_and_upsert_keeps_position(store):
    graph = CollectivizeGraph(store)
    a, b, c = (make_person(graph, n) for n in "ABC")
    store.save_node(replace(a, profile="edited"))
    assert [p.id for p in store.nodes(Person)] == [a.id, b.id, c.id]
    store.delete_node(a.id)
    store.save_node(a)
    assert [p.id for p in store.nodes(Person)] == [b.id, c.id, a.id]


def test_same_id_can_change_node_type(store):
    graph = CollectivizeGraph(store)
    person = make_person(graph, "P")
    store.save_node(Organization(id=person.id, name="Org", kind=OrgKind.CLUB,
                                 provenance=staff_provenance()))
    assert store.nodes(Person) == []
    with pytest.raises(NotFoundError):
        store.node(person.id, Person)
    assert isinstance(store.any_node(person.id), Organization)


def test_edges_survive_endpoint_deletion_identically(store):
    graph = CollectivizeGraph(store)
    a, b = make_person(graph, "A"), make_person(graph, "B")
    edge = store.save_edge(Connected(source_id=a.id, target_id=b.id, source_accepted=True,
                                     provenance=staff_provenance()))
    store.delete_node(b.id)
    assert [e.id for e in store.edges_touching(b.id)] == [edge.id]
    assert store.edge(edge.id, Connected) == edge
    with pytest.raises(NotFoundError):  # re-saving needs both endpoints again
        store.save_edge(edge.removed())


def test_inactive_edges_show_in_touching_but_not_default_queries(store):
    graph = CollectivizeGraph(store)
    a, b = make_person(graph, "A"), make_person(graph, "B")
    edge = store.save_edge(Connected(source_id=a.id, target_id=b.id, source_accepted=True,
                                     provenance=staff_provenance()).removed())
    assert store.edges(Connected) == []
    assert store.edges(Connected, include_inactive=True) == [edge]
    assert store.edges_touching(a.id) == [edge]


def test_edge_and_node_with_same_id_do_not_collide(store):
    graph = CollectivizeGraph(store)
    a, b = make_person(graph, "A"), make_person(graph, "B")
    edge = store.save_edge(Connected(id=a.id, source_id=a.id, target_id=b.id,
                                     provenance=staff_provenance()))
    assert store.node(a.id, Person) == a
    assert store.edge(a.id, Connected) == edge


def test_rollback_restores_overwrites_and_deletes_exactly(store):
    graph = CollectivizeGraph(store)
    a, b = make_person(graph, "A"), make_person(graph, "B")
    before = sorted(store.nodes(Person), key=lambda p: p.id)
    with pytest.raises(RuntimeError), store.transaction():
        store.save_node(replace(a, profile="changed"))
        store.delete_node(b.id)
        make_person(graph, "C")
        raise RuntimeError
    assert sorted(store.nodes(Person), key=lambda p: p.id) == before


def test_keyboard_interrupt_rolls_back_too(store):
    graph = CollectivizeGraph(store)
    with pytest.raises(KeyboardInterrupt), store.transaction():
        make_person(graph, "A")
        raise KeyboardInterrupt
    assert store.nodes(Person) == []


def test_transaction_usable_again_after_failure(store):
    graph = CollectivizeGraph(store)
    with pytest.raises(ValueError), store.transaction(), store.transaction():
        raise ValueError
    with store.transaction():
        make_person(graph, "A")
    assert len(store.nodes(Person)) == 1


def test_sqlite_close_mid_transaction_discards_uncommitted_work(tmp_path):
    path = tmp_path / "t.db"
    store = SqliteGraphStore(path, Environment.TEST)
    person = Person(display_name="P", provenance=staff_provenance())
    block = store.transaction()
    block.__enter__()
    store.save_node(person)
    store.close()  # never committed
    reopened = SqliteGraphStore(path, Environment.TEST)
    assert not reopened.has_node(person.id)
    reopened.close()
    store._conn = sqlite3.connect(":memory:", isolation_level=None)  # let the block end cleanly
    store._conn.execute("BEGIN")
    block.__exit__(None, None, None)


def test_second_store_does_not_see_uncommitted_writes(tmp_path):
    path = tmp_path / "t.db"
    first = SqliteGraphStore(path, Environment.TEST)
    person = Person(display_name="P", provenance=staff_provenance())
    with first.transaction():
        first.save_node(person)
        second = SqliteGraphStore(path, Environment.TEST)
        assert not second.has_node(person.id)
        second.close()
    second = SqliteGraphStore(path, Environment.TEST)
    assert second.has_node(person.id)
    second.close()
    first.close()


def test_failed_commit_does_not_swallow_later_writes(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite_store_module.sqlite3, "connect",
                        lambda *a, **k: real_connect(*a, timeout=0.05, **k))
    path = tmp_path / "t.db"
    store = SqliteGraphStore(path, Environment.TEST)
    reader = real_connect(path, isolation_level=None, timeout=0.05)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM nodes").fetchall()  # holds a shared lock
    with pytest.raises(sqlite3.OperationalError), store.transaction():
        store.save_node(Person(display_name="A", provenance=staff_provenance()))
    reader.execute("COMMIT")
    reader.close()
    later = store.save_node(Person(display_name="B", provenance=staff_provenance()))
    store.close()
    reopened = SqliteGraphStore(path, Environment.TEST)
    try:
        assert reopened.has_node(later.id)
    finally:
        reopened.close()


# --- environment stamping & config ----------------------------------------------------


def test_memory_database_is_stamped_but_never_shared():
    first = SqliteGraphStore(":memory:", Environment.PRODUCTION)
    second = SqliteGraphStore(":memory:", Environment.TEST)  # separate database: no mismatch
    assert second.environment is Environment.TEST
    first.close()
    second.close()


def test_mismatch_leaves_stamp_and_data_untouched(tmp_path):
    path = tmp_path / "p.db"
    store = SqliteGraphStore(path, Environment.PRODUCTION)
    person = store.save_node(Person(display_name="Real", provenance=staff_provenance()))
    store.close()
    for _ in range(2):
        with pytest.raises(EnvironmentMismatchError):
            SqliteGraphStore(path, Environment.TEST)
    store = SqliteGraphStore(path, Environment.PRODUCTION)
    assert store.has_node(person.id)
    assert store._conn.execute("SELECT value FROM meta").fetchall() == [("production",)]
    store.close()


def test_open_graph_closes_store_even_when_the_body_raises(tmp_path):
    config = DatabaseConfig(Environment.TEST, tmp_path / "t.db")
    with pytest.raises(RuntimeError), open_graph(config) as graph:
        store = graph.store
        raise RuntimeError
    with pytest.raises(sqlite3.ProgrammingError):
        store.has_node("x")


@pytest.mark.parametrize("raw", ["", "   ", "prod", "TEST ", "Production\n"])
def test_env_values(raw):
    if raw.strip().lower() in ("test", "production"):
        assert load_config({ENV_VAR: raw}).environment.value == raw.strip().lower()
    else:
        with pytest.raises(ConfigError):
            load_config({ENV_VAR: raw})


def test_odd_db_paths_are_kept_verbatim(tmp_path):
    odd = tmp_path / "spaces and ünïcode" / "d b.db"
    config = load_config({DB_PATH_VAR: f"  {odd}  "})
    assert config.path == odd
    store = SqliteGraphStore(config.path, config.environment)  # creates parent folders
    store.close()
    assert odd.exists()


# --- seeding ------------------------------------------------------------------------------


def _shape(graph: CollectivizeGraph):
    store = graph.store
    return (
        sorted(p.display_name for p in store.nodes(Person)),
        sorted(e.title for e in store.nodes(Event)),
        sorted((p.display_name, p.analytics_consent) for p in store.nodes(Person)),
        len(store.edges(MemberOf, include_inactive=True)),
        len(store.edges(Connected, include_inactive=True)),
        sorted(e.status.value for e in store.nodes(Event)),
    )


def test_seed_is_deterministic_apart_from_ids():
    first, second = (CollectivizeGraph(InMemoryGraphStore()) for _ in range(2))
    seed_mock_data(first, Environment.TEST, NOW)
    seed_mock_data(second, Environment.TEST, NOW)
    assert _shape(first) == _shape(second)


def test_seed_refuses_production_before_writing_anything():
    graph = CollectivizeGraph(InMemoryGraphStore())
    with pytest.raises(ProductionSeedError):
        seed_mock_data(graph, Environment.PRODUCTION, NOW)
    assert graph.store.nodes(Person) == []


def test_seed_refuses_a_production_store_even_if_told_test(tmp_path):
    store = SqliteGraphStore(tmp_path / "production.db", Environment.PRODUCTION)
    try:
        with pytest.raises(ProductionSeedError):
            seed_mock_data(CollectivizeGraph(store), Environment.TEST, NOW)
    finally:
        store.close()


# --- ML export -------------------------------------------------------------------------


def _consent(world, *people):
    for person in people:
        stored = world.graph.store.node(person.id, Person)
        world.graph.store.save_node(replace(stored, analytics_consent=True))


def test_key_length_boundary_is_exactly_sixteen_bytes(world, monkeypatch):
    world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    with pytest.raises(ConfigError):
        world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY[:-1])
    monkeypatch.setenv(EXPORT_KEY_ENV, "é" * 8)  # 8 characters, 16 bytes
    assert len(export_key_from_env()) == MIN_KEY_BYTES
    monkeypatch.setenv(EXPORT_KEY_ENV, "é" * 7 + "a")  # 15 bytes
    with pytest.raises(ConfigError):
        export_key_from_env()


def test_pseudonyms_are_stable_key_dependent_and_hide_ids(world):
    _consent(world, world.alice, world.bob)
    first = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    again = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    other = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY[::-1])
    ids = lambda d: sorted(r["person"] for r in d.people)
    assert ids(first) == ids(again) and set(ids(first)).isdisjoint(ids(other))
    dumped = json.dumps(first.tables())
    for person in (world.alice, world.bob, world.carol):
        assert person.id not in dumped and person.display_name not in dumped


@FAST
@given(ids=st.lists(st.text(min_size=1), min_size=2, max_size=30, unique=True))
def test_pseudonyms_do_not_collide(ids):
    assert len({_pseudonym(KEY, i) for i in ids}) == len(ids)


def test_people_without_activity_still_get_a_row(world):
    _consent(world, world.carol)
    data = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    assert len(data.people) == 1 and data.interactions == [] and data.memberships == []


def test_revoking_consent_removes_person_from_later_exports(world):
    _consent(world, world.alice)
    assert len(world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY).people) == 1
    stored = world.graph.store.node(world.alice.id, Person)
    world.graph.store.save_node(replace(stored, analytics_consent=False))
    later = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    assert later.people == [] and later.memberships == []


@pytest.mark.parametrize("staff", ["staff/../../etc", "staff\nid", "💼", "' OR 1=1 --"])
def test_odd_staff_ids_are_recorded_verbatim(world, staff):
    world.graph.data_access.ml_dataset(staff, PURPOSE, NOW, KEY)
    [record] = world.graph.store.nodes(DataExport)
    assert record.requested_by == staff


@pytest.mark.parametrize("staff", ["", "   ", "\t\n"])
def test_blank_staff_id_is_refused(world, staff):
    with pytest.raises(DomainRuleError):
        world.graph.data_access.ml_dataset(staff, PURPOSE, NOW, KEY)
    assert world.graph.store.nodes(DataExport) == []


def test_purpose_whitespace_is_trimmed_and_counted_after_trimming(world):
    with pytest.raises(DomainRuleError):
        world.graph.data_access.ml_dataset("staff", "   short   ", NOW, KEY)
    data = world.graph.data_access.ml_dataset("staff", f"  {PURPOSE}\n", NOW, KEY)
    assert data.purpose == PURPOSE
    assert world.graph.store.nodes(DataExport)[0].purpose == PURPOSE


def test_ml_dataset_is_json_serializable_with_unicode(world):
    _consent(world, world.alice)
    event = replace(make_event("Café ☕ night"), coordinates=GeoPoint(-89.9, 179.9))
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    world.graph.events.publish(event.id, world.alice.id, NOW)
    world.graph.rsvps.respond(event.id, world.alice.id, RsvpStatus.GOING, NOW)
    data = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    json.loads(json.dumps(data.tables(), ensure_ascii=False))
    assert data.interactions[0]["event_id"] == event.id


def test_private_draft_of_a_non_consenting_person_is_not_exported(world):
    _consent(world, world.alice)
    secret = replace(make_event("Carol's birthday at home"), visibility=Visibility.PRIVATE,
                     coordinates=GeoPoint(42.0, -71.0))
    world.graph.events.create(secret, world.carol.id, world.carol.id)
    data = world.graph.data_access.ml_dataset("staff", PURPOSE, NOW, KEY)
    dumped = json.dumps(data.tables())
    assert secret.id not in dumped and "birthday" not in dumped


def test_person_data_for_someone_else_needs_a_real_purpose(world):
    with pytest.raises(DomainRuleError):
        world.graph.data_access.person_data(world.alice.id, "staff", NOW, staff_purpose="   ")
    assert world.graph.store.nodes(DataExport) == []


# --- scripts: export --------------------------------------------------------------------------


@pytest.fixture
def export_script(monkeypatch, tmp_path, db_env):
    module = load_script("export_ml_dataset")
    root = tmp_path / "exports"
    monkeypatch.setattr(module, "EXPORT_ROOT", root)
    monkeypatch.setenv(EXPORT_KEY_ENV, KEY.decode())
    return module


def _seeded_db(path: Path) -> None:
    with open_graph(DatabaseConfig(Environment.TEST, path)) as graph:
        seed_mock_data(graph, Environment.TEST, NOW)


@pytest.mark.parametrize("name", ["..", "../escape", "a/../../escape", ".", "/etc/x", "a/./../.."])
def test_export_refuses_names_outside_the_export_root(export_script, name, tmp_path):
    with pytest.raises(ValueError):
        export_script.safe_output_dir(name, "stamp")
    assert export_script.main(["--staff", "s", "--purpose", PURPOSE, "--name", name]) == 1


def test_export_accepts_absolute_path_inside_root(export_script):
    inside = export_script.EXPORT_ROOT / "ok"
    assert export_script.safe_output_dir(str(inside), "stamp") == inside.resolve()


def test_export_refuses_symlink_escaping_the_root(export_script, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    export_script.EXPORT_ROOT.mkdir()
    (export_script.EXPORT_ROOT / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        export_script.safe_output_dir("link", "stamp")
    with pytest.raises(ValueError):
        export_script.safe_output_dir("link/sub", "stamp")


def test_export_null_byte_name_is_refused_cleanly(export_script):
    assert export_script.main(["--staff", "s", "--purpose", PURPOSE, "--name", "a\x00b"]) in (1, 2)


def test_export_writes_files_and_refuses_existing_folder(export_script, db_env):
    _seeded_db(db_env)
    args = ["--staff", "staff-1", "--purpose", PURPOSE, "--name", "run1"]
    assert export_script.main(args) == 0
    out = export_script.EXPORT_ROOT / "run1"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["environment"] == "test" and manifest["counts"]["people"] > 0
    for table, count in manifest["counts"].items():
        assert len((out / f"{table}.jsonl").read_text().splitlines()) == count
    assert export_script.main(args) == 1


def test_export_without_a_key_writes_nothing(export_script, monkeypatch, db_env):
    _seeded_db(db_env)
    monkeypatch.setenv(EXPORT_KEY_ENV, "short")
    assert export_script.main(["--staff", "s", "--purpose", PURPOSE, "--name", "x"]) == 1
    assert not export_script.EXPORT_ROOT.exists()


def test_export_with_short_purpose_writes_nothing(export_script, db_env):
    _seeded_db(db_env)
    assert export_script.main(["--staff", "s", "--purpose", "  tiny  ", "--name", "x"]) == 1
    assert not (export_script.EXPORT_ROOT / "x").exists()


def test_export_refuses_a_missing_database(export_script, db_env):
    assert not db_env.exists()
    assert export_script.main(["--staff", "s", "--purpose", PURPOSE, "--name", "x"]) == 1
    assert not db_env.exists()


# --- scripts: seed ------------------------------------------------------------------------------


@pytest.fixture
def seed_script(db_env):
    return load_script("seed_test_db")


def test_seed_script_creates_then_refuses_without_reset(seed_script, db_env):
    assert seed_script.main([]) == 0
    assert seed_script.main([]) == 1
    assert seed_script.main(["--reset"]) == 0


def test_seed_script_refuses_production_env(seed_script, monkeypatch, db_env):
    monkeypatch.setenv(ENV_VAR, "production")
    assert seed_script.main(["--reset"]) == 1
    assert not db_env.exists()


def test_seed_script_reset_keeps_a_production_stamped_file(seed_script, db_env):
    store = SqliteGraphStore(db_env, Environment.PRODUCTION)
    person = store.save_node(Person(display_name="Real", provenance=staff_provenance()))
    store.close()
    assert seed_script.main(["--reset"]) == 1
    store = SqliteGraphStore(db_env, Environment.PRODUCTION)
    assert store.has_node(person.id)
    store.close()


def test_seed_script_reset_keeps_an_unstamped_database(seed_script, db_env):
    db_env.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_env)
    conn.execute("CREATE TABLE precious (v TEXT)")
    conn.execute("INSERT INTO precious VALUES ('keep me')")
    conn.commit()
    conn.close()
    assert seed_script.main(["--reset"]) == 1
    assert sqlite3.connect(db_env).execute("SELECT v FROM precious").fetchall() == [("keep me",)]


def test_seed_script_reset_on_a_non_database_file_does_not_delete_it(seed_script, db_env):
    db_env.parent.mkdir(parents=True, exist_ok=True)
    db_env.write_text("not a database, just notes " * 100)
    assert seed_script.main(["--reset"]) == 1  # refuses cleanly: it isn't a test database
    assert db_env.read_text().startswith("not a database")


# --- scripts: check_graph & ai_search --------------------------------------------------------


def test_check_graph_reports_missing_database(db_env):
    assert load_script("check_graph").main() == 1
    assert not db_env.exists()


def test_check_graph_reports_corrupt_json_cleanly(db_env):
    _seeded_db(db_env)
    conn = sqlite3.connect(db_env)
    conn.execute("UPDATE nodes SET data = '{not json' WHERE rowid = 1")
    conn.commit()
    conn.close()
    assert load_script("check_graph").main() == 1


def test_check_graph_reports_record_missing_a_field_cleanly(db_env):
    _seeded_db(db_env)
    conn = sqlite3.connect(db_env)
    conn.execute("UPDATE nodes SET data = json_remove(data, '$.display_name') WHERE type = 'Person'")
    conn.commit()
    conn.close()
    assert load_script("check_graph").main() == 1


def test_check_graph_on_a_fresh_seed_is_clean(db_env):
    _seeded_db(db_env)
    assert load_script("check_graph").main() == 0


def test_ai_search_refuses_missing_database_and_unknown_person(db_env):
    script = load_script("ai_search")
    assert script.main(["anything"]) == 1
    assert not db_env.exists()
    _seeded_db(db_env)
    assert script.main(["--as", "Nobody-Here", "--single", "anything"]) == 1


def test_scripts_do_not_leave_extra_sys_path_breakage():
    assert any(p.endswith("src") for p in sys.path)


def test_tag_slug_rules_hold_after_decoding():
    data = encode_record(Tag(slug="ok-slug", label="Ok", provenance=staff_provenance()))
    with pytest.raises(DomainRuleError):
        decode_record("Tag", data | {"slug": "Not OK"})


def test_provenance_naive_created_by_default_is_aware():
    assert Provenance(created_by=None, method=ProvenanceMethod.STAFF).created_at.tzinfo is UTC
