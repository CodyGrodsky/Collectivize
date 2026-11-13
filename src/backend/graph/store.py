"""Storage interface for nodes and edges, plus an in-memory implementation.

Records are immutable; a store maps ids to each record's latest version.
`GraphStore` is the interface every service depends on. Implementations:

- `InMemoryGraphStore` (here): fast, non-persistent; used by unit tests.
- `SqliteGraphStore` (sqlite_store.py): persistent; used for the test and
  production databases.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, TypeVar

from backend.objects import Edge, GraphRecord

from .errors import NotFoundError

N = TypeVar("N", bound=GraphRecord)
E = TypeVar("E", bound=Edge)


class GraphStore(Protocol):
    def save_node(self, node: N) -> N: ...
    def has_node(self, node_id: str) -> bool: ...
    def node(self, node_id: str, cls: type[N]) -> N: ...
    def any_node(self, node_id: str) -> GraphRecord: ...
    def delete_node(self, node_id: str) -> None: ...
    def nodes(self, cls: type[N]) -> list[N]: ...
    def save_edge(self, edge: E) -> E: ...
    def edge(self, edge_id: str, cls: type[E]) -> E: ...
    def edges(
        self,
        cls: type[E],
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[E]: ...
    def edges_touching(self, node_id: str) -> list[Edge]: ...
    def delete_edge(self, edge_id: str) -> None: ...
    def transaction(self) -> AbstractContextManager[None]: ...
    def close(self) -> None: ...


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._nodes: dict[str, GraphRecord] = {}
        self._edges: dict[str, Edge] = {}

    # --- nodes ---------------------------------------------------------------

    def save_node(self, node: N) -> N:
        if isinstance(node, Edge):
            raise TypeError("use save_edge for edges")
        self._nodes[node.id] = node
        return node

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def node(self, node_id: str, cls: type[N]) -> N:
        found = self._nodes.get(node_id)
        if not isinstance(found, cls):
            raise NotFoundError(f"{cls.__name__} {node_id!r} not found")
        return found

    def any_node(self, node_id: str) -> GraphRecord:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise NotFoundError(f"node {node_id!r} not found") from None

    def delete_node(self, node_id: str) -> None:
        """Hard delete. Reserved for account erasure."""
        self._nodes.pop(node_id, None)

    def nodes(self, cls: type[N]) -> list[N]:
        return [n for n in self._nodes.values() if isinstance(n, cls)]

    # --- edges ---------------------------------------------------------------

    def save_edge(self, edge: E) -> E:
        for endpoint in (edge.source_id, edge.target_id):
            if endpoint not in self._nodes:
                raise NotFoundError(f"edge endpoint {endpoint!r} not found")
        self._edges[edge.id] = edge
        return edge

    def edge(self, edge_id: str, cls: type[E]) -> E:
        found = self._edges.get(edge_id)
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
        return [
            e
            for e in self._edges.values()
            if isinstance(e, cls)
            and (source_id is None or e.source_id == source_id)
            and (target_id is None or e.target_id == target_id)
            and (include_inactive or e.is_active)
        ]

    def edges_touching(self, node_id: str) -> list[Edge]:
        return [
            e for e in self._edges.values() if node_id in (e.source_id, e.target_id)
        ]

    def delete_edge(self, edge_id: str) -> None:
        """Hard delete. Reserved for account erasure."""
        self._edges.pop(edge_id, None)

    # --- lifecycle -----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All-or-nothing: restores the previous state if the block raises.
        Records are immutable, so shallow copies are a full snapshot."""
        nodes, edges = dict(self._nodes), dict(self._edges)
        try:
            yield
        except BaseException:
            self._nodes, self._edges = nodes, edges
            raise

    def close(self) -> None:
        """Nothing to release."""
