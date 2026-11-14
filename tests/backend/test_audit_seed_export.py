"""Audit of the seeders, the ML export and the command-line scripts.

Every test above the "coverage" marker fails against the code as audited
because of a real bug; the ones below it pin down behaviour that is right.
No test touches data/ (each DB lives under tmp_path) and none makes an API call.
"""

import importlib.util
import json
import logging
import sqlite3
from pathlib import Path

import pytest

import backend.graph.claude_interpreter as claude_interpreter
from backend.config import DB_PATH_VAR, ENV_VAR, DatabaseConfig, Environment
from backend.database import open_graph
from backend.graph import CollectivizeGraph
from backend.graph.sqlite_store import read_environment_stamp
from backend.objects import Event, InterestedIn, MemberOf, Person, Rsvp, Tag
from backend.seed import seed_mock_data
from backend.seed_bulk import SMALL

from .conftest import NOW

ROOT = Path(__file__).resolve().parents[2]


def _script(name):
    """Import scripts/<name>.py as a module (the scripts aren't a package)."""
    spec = importlib.util.spec_from_file_location(f"audit_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_world(bulk=None):
    """The seeded world with random ids replaced by stable labels."""
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW, bulk)
    store = graph.store
    label = {p.id: p.display_name for p in store.nodes(Person)}
    label |= {t.id: t.slug for t in store.nodes(Tag)}
    label |= {e.id: f"{e.title}@{e.starts_at.isoformat()}" for e in store.nodes(Event)}
    rsvps = sorted((label[r.source_id], label[r.target_id], r.status.value)
                   for r in store.edges(Rsvp))
    interests = sorted((label[i.source_id], label[i.target_id])
                       for i in store.edges(InterestedIn))
    travel = sorted((p.display_name, p.travel_samples, p.travel_log_km)
                    for p in store.nodes(Person))
    return rsvps, interests, travel


@pytest.fixture
def no_credentials(monkeypatch):
    """No API key from the environment or the Keychain (the conftest also
    blocks the Anthropic client), so AI search always falls back locally."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_interpreter, "anthropic_api_key", lambda: None)


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "test.db"
    with open_graph(DatabaseConfig(Environment.TEST, path)) as graph:
        seed_mock_data(graph, Environment.TEST, NOW)
    return path


@pytest.fixture
def use_db(monkeypatch):
    def point_at(path, env="test"):
        monkeypatch.setenv(ENV_VAR, env)
        monkeypatch.setenv(DB_PATH_VAR, str(path))
    return point_at


def _results(caplog):
    lines = [r.getMessage() for r in caplog.records]
    return lines[lines.index("Results:") + 1:]


# --- seed determinism ----------------------------------------------------------


def test_demo_seed_rsvps_interests_and_travel_are_the_same_every_time():
    """Seeding the demo twice gives the same RSVPs, interests and travel habits."""
    first, again = _canonical_world(), _canonical_world()
    assert first[0] == again[0], "RSVPs differ between two seeds"
    assert first[1] == again[1], "interests differ between two seeds"
    assert first[2] == again[2], "learned travel differs between two seeds"


# --- seeded timeline -----------------------------------------------------------


def test_bulk_memberships_never_start_before_the_person_existed():
    """No generated membership or claim is dated before its person's account was created."""
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW, SMALL)
    born = {p.id: p.provenance.created_at for p in graph.store.nodes(Person)}
    early = [
        m for m in graph.store.edges(MemberOf, include_inactive=True)
        if m.started_at < born[m.source_id]
    ]
    assert early == []


# --- scripts/check_graph.py ------------------------------------------------------


def test_check_graph_never_changes_an_unstamped_file(tmp_path, use_db):
    """check_graph is documented read-only: it must not create tables in or stamp a file."""
    path = tmp_path / "unstamped.db"
    sqlite3.connect(path).close()  # an empty file, not a Collectivize database yet
    use_db(path)
    _script("check_graph").main()
    assert path.stat().st_size == 0
    assert read_environment_stamp(path) is None


def test_check_graph_reports_a_wrong_environment_database_cleanly(tmp_path, use_db):
    """check_graph exits 1 (not a traceback) on a DB stamped for the other environment."""
    path = tmp_path / "prod.db"
    with open_graph(DatabaseConfig(Environment.PRODUCTION, path)):
        pass
    use_db(path, env="test")
    assert _script("check_graph").main() == 1


def test_check_graph_reports_a_file_that_is_not_a_database_cleanly(tmp_path, use_db):
    """check_graph exits 1 (not a traceback) when the DB path holds some other file."""
    path = tmp_path / "notes.db"
    path.write_text("not a database " * 100)
    use_db(path)
    assert _script("check_graph").main() == 1


def test_scripts_report_a_bad_environment_name_cleanly(tmp_path, use_db):
    """A typo in COLLECTIVIZE_ENV gives exit code 1, not a ConfigError traceback."""
    use_db(tmp_path / "x.db", env="prod")
    assert _script("check_graph").main() == 1


# --- scripts/export_ml_dataset.py ------------------------------------------------


def test_export_reports_a_wrong_environment_database_cleanly(tmp_path, use_db, monkeypatch):
    """The export script exits 1 (not a traceback) on a DB stamped for the other environment."""
    path = tmp_path / "prod.db"
    with open_graph(DatabaseConfig(Environment.PRODUCTION, path)):
        pass
    use_db(path, env="test")
    monkeypatch.setenv("COLLECTIVIZE_EXPORT_KEY", "k" * 32)
    export = _script("export_ml_dataset")
    monkeypatch.setattr(export, "EXPORT_ROOT", tmp_path / "exports")
    assert export.main(["--staff", "s1", "--purpose", "Train the model"]) == 1


# --- scripts/ai_search.py --------------------------------------------------------


def test_ai_search_agent_mode_honours_limit(demo_db, use_db, no_credentials, caplog):
    """`--limit 1` shows at most one result in the default (agent) mode too."""
    use_db(demo_db)
    caplog.set_level(logging.INFO)
    assert _script("ai_search").main(["--limit", "1", "social"]) == 0
    assert len(_results(caplog)) <= 1


def test_ai_search_agent_mode_honours_radius(demo_db, use_db, no_credentials, caplog):
    """`--radius 1` never lists the night market crawl ~6 km away in agent mode."""
    use_db(demo_db)
    caplog.set_level(logging.INFO)
    assert _script("ai_search").main(["--radius", "1", "food night market"]) == 0
    assert not any("Night market food crawl" in line for line in _results(caplog))


@pytest.mark.parametrize("args", [
    ["--single", "--radius", "0", "anything"],
    ["--single", "--radius", "-2", "anything"],
    ["--single", "--limit", "0", "anything"],
])
def test_ai_search_bad_numbers_fail_cleanly(demo_db, use_db, no_credentials, args):
    """Out-of-range --radius/--limit give exit code 1, not a DomainRuleError traceback."""
    use_db(demo_db)
    assert _script("ai_search").main(args) == 1


# --- coverage --------------------------------------------------------------------
# Passing checks of behaviour the audit found correct.


@pytest.mark.parametrize("bulk", [None, SMALL], ids=["demo", "small"])
def test_seeded_worlds_pass_the_integrity_audit_after_a_reload(tmp_path, bulk):
    """Seeded worlds of each small size reload from SQLite with no integrity issues."""
    config = DatabaseConfig(Environment.TEST, tmp_path / "test.db")
    with open_graph(config) as graph:
        seed_mock_data(graph, Environment.TEST, NOW, bulk)
    with open_graph(config) as graph:
        assert graph.integrity.audit() == ()


def test_bulk_part_of_the_world_is_deterministic():
    """Memberships of the generated world are identical across two seeds."""
    def memberships():
        graph = CollectivizeGraph()
        seed_mock_data(graph, Environment.TEST, NOW, SMALL)
        name = {p.id: p.display_name for p in graph.store.nodes(Person)}
        return sorted((name[m.source_id], m.role.value, m.org_confirmed)
                      for m in graph.store.edges(MemberOf, include_inactive=True))
    assert memberships() == memberships()


def test_export_of_the_seeded_world_leaks_no_names_or_person_ids():
    """The ML export of a seeded world contains no display names or real person ids."""
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW, SMALL)
    data = graph.data_access.ml_dataset("staff", "Train the attendance model", NOW, b"k" * 32)
    dumped = json.dumps(data.tables())
    people = graph.store.nodes(Person)
    assert data.people
    assert not [p.id for p in people if p.id in dumped]
    assert not [p.display_name for p in people if p.display_name.split(" (")[0] in dumped]
    assert len(data.people) == sum(p.analytics_consent for p in people)


def test_export_after_erasure_drops_every_trace_of_the_person():
    """An erased person's pseudonym appears in no table of a later export."""
    from backend.graph.data_access import _pseudonym

    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    key = b"k" * 32
    logan = result.people["logan"]  # a consenting demo student with friends and RSVPs
    assert _pseudonym(key, logan) in json.dumps(
        graph.data_access.ml_dataset("staff", "Train the attendance model", NOW, key).tables())
    graph.privacy.erase_person(logan)
    later = graph.data_access.ml_dataset("staff", "Train the attendance model", NOW, key)
    assert _pseudonym(key, logan) not in json.dumps(later.tables())


@pytest.mark.parametrize("name", ["..", "/etc", "../../src", "."])
def test_export_refuses_folders_outside_data_exports(name):
    """--name can never point the export outside data/exports."""
    export = _script("export_ml_dataset")
    with pytest.raises(ValueError):
        export.safe_output_dir(name, "stamp")


def test_seed_script_refuses_to_reset_an_unstamped_file(tmp_path, use_db):
    """seed_test_db --reset leaves a file that isn't stamped test untouched."""
    path = tmp_path / "other.db"
    path.write_bytes(b"precious")
    use_db(path)
    assert _script("seed_test_db").main(["--reset", "--size", "demo"]) == 1
    assert path.read_bytes() == b"precious"
