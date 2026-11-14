"""Reading the database reuses decoded records (sqlite_store._decode_cached).
The key is the stored text, so a changed record is never served stale."""

from dataclasses import replace

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.graph.sqlite_store import SqliteGraphStore, _decode_cached
from backend.objects import Person

from .conftest import make_person


@pytest.fixture
def store():
    graph = CollectivizeGraph(SqliteGraphStore(":memory:", Environment.TEST))
    return graph.store, make_person(graph, "Dana")


def test_reading_the_same_record_twice_decodes_it_once(store):
    db, dana = store
    _decode_cached.cache_clear()
    first, second = db.node(dana.id, Person), db.node(dana.id, Person)
    assert first is second and _decode_cached.cache_info().misses == 1


def test_a_saved_change_is_read_back_at_once(store):
    db, dana = store
    db.node(dana.id, Person)  # remembered
    db.save_node(replace(db.node(dana.id, Person), bio="Likes kites."))
    assert db.node(dana.id, Person).bio == "Likes kites."


def test_bad_stored_text_fails_every_time(store):
    db, dana = store
    db._conn.execute("UPDATE nodes SET data = '{\"nope\": 1}' WHERE id = ?", (dana.id,))
    for _ in range(2):
        with pytest.raises(ValueError):
            db.node(dana.id, Person)
