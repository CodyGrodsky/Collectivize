"""Open the graph backed by the configured (test or production) database.

    from backend.database import open_graph

    with open_graph() as graph:          # uses COLLECTIVIZE_ENV
        graph.events.publish(...)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from backend.config import DatabaseConfig, load_config
from backend.graph import CollectivizeGraph
from backend.graph.sqlite_store import SqliteGraphStore


@contextmanager
def open_graph(config: DatabaseConfig | None = None) -> Iterator[CollectivizeGraph]:
    config = config or load_config()
    store = SqliteGraphStore(config.path, config.environment)
    try:
        yield CollectivizeGraph(store)
    finally:
        store.close()
