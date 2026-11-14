"""SQLite-backed GraphStore used for the test and production databases.

Each database file is stamped with its environment the first time it is
opened, and refuses to open under a different one. That way the test
configuration can never write into the production file, or the reverse.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from backend.config import Environment
from backend.objects import Edge, GraphRecord

from .codec import decode_record, encode_record, type_names_for
from .errors import NotFoundError
from .store import check_edge_endpoints

N = TypeVar("N", bound=GraphRecord)
E = TypeVar("E", bound=Edge)

IN_MEMORY = ":memory:"
# How many decoded records to remember (see `_decode_cached`).
DECODE_CACHE_SIZE = 100_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id   TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    id        TEXT PRIMARY KEY,
    type      TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    state     TEXT NOT NULL,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS nodes_by_type ON nodes (type);
CREATE INDEX IF NOT EXISTS edges_by_source ON edges (source_id, type);
CREATE INDEX IF NOT EXISTS edges_by_target ON edges (target_id, type);
"""


class EnvironmentMismatchError(RuntimeError):
    """A database file was opened under the wrong environment."""


def read_environment_stamp(path: Path | str) -> str | None:
    """The environment a database file is stamped with, read without changing
    the file. None if it isn't a stamped Collectivize database."""
    try:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'environment'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


class SqliteGraphStore:
    def __init__(self, path: Path | str, environment: Environment) -> None:
        if str(path) != IN_MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.environment = environment
        # Autocommit mode: this class issues BEGIN / SAVEPOINT itself, so
        # nested transaction() blocks can roll back independently.
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._depth = 0
        self._conn.executescript(SCHEMA)
        self._stamp_environment(environment)

    # --- nodes ---------------------------------------------------------------

    def save_node(self, node: N) -> N:
        if isinstance(node, Edge):
            raise TypeError("use save_edge for edges")
        self._write(
            "INSERT INTO nodes (id, type, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type = excluded.type, data = excluded.data",
            (node.id, type(node).__name__, _dump(node)),
        )
        return node

    def has_node(self, node_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return row is not None

    def node(self, node_id: str, cls: type[N]) -> N:
        found = self._load_node(node_id)
        if not isinstance(found, cls):
            raise NotFoundError(f"{cls.__name__} {node_id!r} not found")
        return found

    def any_node(self, node_id: str) -> GraphRecord:
        found = self._load_node(node_id)
        if found is None:
            raise NotFoundError(f"node {node_id!r} not found")
        return found

    def delete_node(self, node_id: str) -> None:
        """Hard delete. Reserved for account erasure."""
        self._write("DELETE FROM nodes WHERE id = ?", (node_id,))

    def nodes(self, cls: type[N]) -> list[N]:
        names = type_names_for(cls)
        if not names:
            return []
        sql = f"SELECT type, data FROM nodes WHERE type IN ({', '.join('?' * len(names))}) ORDER BY rowid"
        return [_load(row) for row in self._conn.execute(sql, names)]  # type: ignore[misc]

    # --- edges ---------------------------------------------------------------

    def save_edge(self, edge: E) -> E:
        check_edge_endpoints(edge, self._load_node)
        self._write(
            "INSERT INTO edges (id, type, source_id, target_id, state, data) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type = excluded.type, "
            "source_id = excluded.source_id, target_id = excluded.target_id, "
            "state = excluded.state, data = excluded.data",
            (
                edge.id,
                type(edge).__name__,
                edge.source_id,
                edge.target_id,
                edge.state.value,
                _dump(edge),
            ),
        )
        return edge

    def edge(self, edge_id: str, cls: type[E]) -> E:
        row = self._conn.execute(
            "SELECT type, data FROM edges WHERE id = ?", (edge_id,)
        ).fetchone()
        found = _load(row) if row else None
        if not isinstance(found, cls):
            raise NotFoundError(f"{cls.__name__} {edge_id!r} not found")
        return found

    def edges(
        self,
        cls: type[E],
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[E]:
        names = type_names_for(cls)
        if not names:
            return []
        clauses = [f"type IN ({', '.join('?' * len(names))})"]
        params: list[str] = list(names)
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        if not include_inactive:
            clauses.append("state = 'active'")
        sql = f"SELECT type, data FROM edges WHERE {' AND '.join(clauses)} ORDER BY rowid"
        return [_load(row) for row in self._conn.execute(sql, params)]  # type: ignore[misc]

    def edges_touching(self, node_id: str) -> list[Edge]:
        rows = self._conn.execute(
            "SELECT type, data FROM edges WHERE source_id = ? OR target_id = ? ORDER BY rowid",
            (node_id, node_id),
        )
        return [_load(row) for row in rows]  # type: ignore[misc]

    def delete_edge(self, edge_id: str) -> None:
        """Hard delete. Reserved for account erasure."""
        self._write("DELETE FROM edges WHERE id = ?", (edge_id,))

    # --- lifecycle -----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All-or-nothing: rolls back every write in the block if it raises.

        The outermost block is a real transaction (BEGIN ... COMMIT). Each
        nested block is a SAVEPOINT, so a failure caught inside an outer
        block undoes only the nested block's writes, matching the
        in-memory store."""
        savepoint = f"sp{self._depth}"
        is_outer = self._depth == 0
        self._conn.execute("BEGIN" if is_outer else f"SAVEPOINT {savepoint}")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if is_outer:
                self._conn.execute("ROLLBACK")
            else:
                self._conn.execute(f"ROLLBACK TO {savepoint}")
                self._conn.execute(f"RELEASE {savepoint}")
            raise
        self._depth -= 1
        try:
            self._conn.execute("COMMIT" if is_outer else f"RELEASE {savepoint}")
        except BaseException:
            # e.g. "database is locked": don't leave the connection inside an
            # open transaction, where later writes would silently be lost.
            if is_outer and self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    # --- internals -----------------------------------------------------------

    def _write(self, sql: str, params: tuple[str, ...]) -> None:
        """Outside a transaction() block each write commits on its own."""
        self._conn.execute(sql, params)

    def _load_node(self, node_id: str) -> GraphRecord | None:
        row = self._conn.execute(
            "SELECT type, data FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return _load(row) if row else None

    def _stamp_environment(self, environment: Environment) -> None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'environment'"
        ).fetchone()
        if row is None:
            self._write(
                "INSERT INTO meta (key, value) VALUES ('environment', ?)",
                (environment.value,),
            )
        elif row[0] != environment.value:
            self._conn.close()
            raise EnvironmentMismatchError(
                f"this is a {row[0]} database; refusing to open it as {environment.value}"
            )


def _dump(record: GraphRecord) -> str:
    return json.dumps(encode_record(record), separators=(",", ":"))


def _load(row: tuple[str, str]) -> GraphRecord:
    type_name, data = row
    return _decode_cached(type_name, data)


@lru_cache(maxsize=DECODE_CACHE_SIZE)
def _decode_cached(type_name: str, data: str) -> GraphRecord:
    """Decoding is the slow part of reading (one search reads the same
    hosts, RSVPs and tags thousands of times), so each distinct stored text
    is decoded once and the record reused. This is safe because records are
    frozen, and it can never serve stale data: the key is the stored text
    itself, so a record that changes is a different key. Text that fails
    validation raises every time (errors are not remembered)."""
    return decode_record(type_name, json.loads(data))
