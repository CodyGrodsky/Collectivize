"""The network view: people and orgs as nodes, linked by accepted
connections (person <-> person) and confirmed memberships (person -> org).

Everything here is computed per viewer, using only links that viewer is
allowed to see. Nothing hidden is drawn as a placeholder; it simply isn't
there.

- A connection is a link if the viewer is one of the two people, or if
  neither person has turned on `hide_connections` and the viewer may see
  the edge and both people.
- A membership is a link if the viewer is that member, or if the org's
  member list shows it to the viewer (see RosterPolicy). Pending requests
  and past memberships are never links.
- Orgs and people the viewer can't see never appear.

The map (`node_map`) is what you get when you tap a node: that node at
the center and what is directly linked to it, limited to nodes you could
open yourself (you, your connections, your orgs). Everything else is left
out, not greyed out. It also says which of the people shown are connected
to each other, which are merely close (they share a connection or an org
besides the center), and how close each node is to the center (how many of the
center's links it shares), so the page can draw close ones together. You
can open it only for yourself, your own connections and orgs you belong
to, so nobody can click their way across the whole network.

Paths are shortest first (at most MAX_PATH_HOPS links). When two paths
are equally short, the one through fewer orgs wins, so paths go through
people where they can.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from itertools import combinations, pairwise

from backend.objects import (
    Connected,
    DomainRuleError,
    GraphRecord,
    MemberOf,
    Organization,
    Person,
)

from .errors import NotFoundError, PermissionDeniedError
from .privacy import PrivacyService
from .roster import RosterService
from .store import GraphStore

MAX_PATH_HOPS = 4
DEFAULT_MAX_NODES = 150
MAX_MAP_PEOPLE = 60
MAX_DEPTH = 3


class NodeKind(Enum):
    PERSON = "person"
    ORG = "org"


class LinkKind(Enum):
    CONNECTION = "connection"
    MEMBERSHIP = "membership"


@dataclass(frozen=True)
class NetworkNode:
    id: str
    kind: NodeKind
    label: str


@dataclass(frozen=True)
class NetworkLink:
    source: str
    target: str
    kind: LinkKind


@dataclass(frozen=True)
class NetworkPath:
    nodes: tuple[NetworkNode, ...]
    links: tuple[NetworkLink, ...]

    @property
    def hops(self) -> int:
        return len(self.links)


@dataclass(frozen=True)
class NetworkView:
    center_id: str
    nodes: tuple[NetworkNode, ...]
    links: tuple[NetworkLink, ...]
    truncated: bool  # True when max_nodes cut the view short
    path: NetworkPath | None = None  # focus view: the route from the viewer


@dataclass(frozen=True)
class InCommon:
    people: tuple[Person, ...]
    orgs: tuple[Organization, ...]

    @property
    def summary(self) -> str:
        """E.g. "2 mutual friends · Surf Club"."""
        parts = []
        if self.people:
            count = len(self.people)
            parts.append(f"{count} mutual friend{'s' if count != 1 else ''}")
        parts.extend(org.name for org in self.orgs)
        return " · ".join(parts)


@dataclass(frozen=True)
class MapOrg:
    """An org on a map: one the viewer belongs to. `closeness`: how many of
    the center's other links it also has (drawn nearer the more it has)."""

    id: str
    name: str
    closeness: int


@dataclass(frozen=True)
class MapPerson:
    """A person on a map: the viewer or one of the viewer's connections.
    `closeness`: how many of the center's other links they also have."""

    id: str
    name: str
    is_viewer: bool
    closeness: int


@dataclass(frozen=True)
class NetworkMap:
    """One node's map for one viewer: the center and what is directly
    linked to it that the viewer can open. Pairs of person ids say how the
    people shown relate to each other: `links` are accepted connections,
    `near` are people who aren't connected but are close in the graph (they
    share a connection or an org, not counting the center). A pair in
    neither has no real tie. `orgs` is empty when the center is an org."""

    center: NetworkNode
    is_self: bool  # the viewer's own map
    orgs: tuple[MapOrg, ...]
    people: tuple[MapPerson, ...]
    links: tuple[tuple[str, str], ...]
    near: tuple[tuple[str, str], ...]
    truncated: bool  # True when there were more than MAX_MAP_PEOPLE people


class NetworkService:
    def __init__(
        self, store: GraphStore, privacy: PrivacyService, roster: RosterService
    ) -> None:
        self._store = store
        self._privacy = privacy
        self._roster = roster

    # --- queries -------------------------------------------------------------

    def neighborhood(
        self,
        viewer_id: str | None,
        center_id: str | None = None,
        *,
        depth: int = 1,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> NetworkView:
        """The center (default: you) plus everything within `depth` links."""
        viewer = self._require_viewer(viewer_id)
        if not 1 <= depth <= MAX_DEPTH or max_nodes < 1:
            raise DomainRuleError(f"depth must be 1-{MAX_DEPTH} and max_nodes positive")
        center = center_id or viewer
        self._visible_node(viewer, center)
        walk = _Walk(self, viewer)
        order, truncated = [center], False
        frontier = [center]
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                for neighbor, _kind in walk.neighbors(node_id):
                    if neighbor in order:
                        continue
                    if len(order) >= max_nodes:
                        truncated = True
                        continue
                    order.append(neighbor)
                    next_frontier.append(neighbor)
            frontier = next_frontier
        return NetworkView(
            center_id=center,
            nodes=tuple(walk.node(n) for n in order),
            links=walk.links_among(order),
            truncated=truncated,
        )

    def node_map(self, viewer_id: str | None, center_id: str | None = None) -> NetworkMap:
        """The map centered on you (the default), one of your connections,
        or an org you belong to."""
        viewer = self._require_viewer(viewer_id)
        center = center_id or viewer
        if isinstance(self._visible_node(viewer, center), Organization):
            return self.org_map(viewer, center)
        return self.person_map(viewer, center)

    def person_map(self, viewer_id: str | None, person_id: str | None = None) -> NetworkMap:
        """You or one of your connections at the center, with the orgs and
        connections of theirs that are also yours. Anyone you aren't
        connected to, and any org you aren't in, is left out entirely."""
        viewer = self._require_viewer(viewer_id)
        center = person_id or viewer
        if not isinstance(self._visible_node(viewer, center), Person):
            raise NotFoundError(f"{center!r} not found")
        walk = _Walk(self, viewer)
        mine = dict(walk.neighbors(viewer))
        if center != viewer and mine.get(center) is not LinkKind.CONNECTION:
            raise PermissionDeniedError("you can only open the network of your own connections")
        return self._build_map(walk, viewer, mine, center)

    def org_map(self, viewer_id: str | None, org_id: str) -> NetworkMap:
        """An org you belong to at the center, with the members who are your
        connections (and you). Other members are left out entirely."""
        viewer = self._require_viewer(viewer_id)
        if not isinstance(self._visible_node(viewer, org_id), Organization):
            raise NotFoundError(f"{org_id!r} not found")
        walk = _Walk(self, viewer)
        mine = dict(walk.neighbors(viewer))
        if mine.get(org_id) is not LinkKind.MEMBERSHIP:
            raise PermissionDeniedError("you can only open organizations you belong to")
        return self._build_map(walk, viewer, mine, org_id)

    @staticmethod
    def _build_map(
        walk: _Walk, viewer: str, mine: dict[str, LinkKind], center: str
    ) -> NetworkMap:
        """Keep only the center's links that the viewer could open next:
        the viewer, the viewer's connections, and the viewer's orgs."""
        around = dict(walk.neighbors(center))

        def closeness(node_id: str) -> int:
            return len(around.keys() & dict(walk.neighbors(node_id)).keys())

        orgs = [
            MapOrg(id=i, name=walk.node(i).label, closeness=closeness(i))
            for i, kind in around.items()
            if kind is LinkKind.MEMBERSHIP and mine.get(i) is LinkKind.MEMBERSHIP
            and walk.is_org(i)
        ]
        people = [
            MapPerson(id=i, name=walk.node(i).label, is_viewer=i == viewer, closeness=closeness(i))
            for i in around
            if not walk.is_org(i) and (i == viewer or mine.get(i) is LinkKind.CONNECTION)
        ]
        people.sort(key=lambda p: (not p.is_viewer, -p.closeness, p.name, p.id))
        shown = people[:MAX_MAP_PEOPLE]
        ids = {p.id for p in shown}
        links = {
            tuple(sorted((p.id, other)))
            for p in shown
            for other, kind in walk.neighbors(p.id)
            if kind is LinkKind.CONNECTION and other in ids
        }
        # Close but not connected: two people who share some other link.
        reach = {p.id: dict(walk.neighbors(p.id)).keys() - {center} for p in shown}
        near = {
            (a, b)
            for a, b in combinations(sorted(ids), 2)
            if (a, b) not in links and reach[a] & reach[b]
        }
        return NetworkMap(
            center=walk.node(center),
            is_self=center == viewer,
            orgs=tuple(sorted(orgs, key=lambda o: (-o.closeness, o.name, o.id))),
            people=tuple(shown),
            links=tuple(sorted(links)),
            near=tuple(sorted(near)),
            truncated=len(people) > MAX_MAP_PEOPLE,
        )

    def find_path(
        self, viewer_id: str | None, target_id: str, *, max_hops: int = MAX_PATH_HOPS
    ) -> NetworkPath | None:
        """Shortest route from you to someone or some org, or None when
        there's no close connection (more than `max_hops` links away)."""
        viewer = self._require_viewer(viewer_id)
        self._visible_node(viewer, target_id)
        walk = _Walk(self, viewer)
        return walk.path(viewer, target_id, max_hops)

    def focus(self, viewer_id: str | None, focus_id: str) -> NetworkView:
        """What the graph shows when you click a node: the node, its direct
        links, and the path from you to it (if there's a close one)."""
        around = self.neighborhood(viewer_id, focus_id)
        path = self.find_path(viewer_id, focus_id)
        if path is None:
            return around
        nodes = {n.id: n for n in around.nodes + path.nodes}
        links = {(l.source, l.target): l for l in around.links + path.links}
        return NetworkView(
            center_id=focus_id,
            nodes=tuple(nodes.values()),
            links=tuple(links.values()),
            truncated=around.truncated,
            path=path,
        )

    def in_common(self, viewer_id: str | None, other_id: str) -> InCommon:
        """Mutual friends and shared orgs, as the viewer is allowed to see them."""
        viewer = self._require_viewer(viewer_id)
        if other_id == viewer:
            raise DomainRuleError("pick someone other than yourself")
        if not isinstance(self._visible_node(viewer, other_id), Person):
            raise DomainRuleError("in-common is only shown between two people")
        walk = _Walk(self, viewer)
        mine, theirs = dict(walk.neighbors(viewer)), dict(walk.neighbors(other_id))
        shared = mine.keys() & theirs.keys()
        people = [self._store.node(i, Person) for i in shared if mine[i] is LinkKind.CONNECTION]
        orgs = [self._store.node(i, Organization) for i in shared if mine[i] is LinkKind.MEMBERSHIP]
        return InCommon(
            people=tuple(sorted(people, key=lambda p: (p.display_name, p.id))),
            orgs=tuple(sorted(orgs, key=lambda o: (o.name, o.id))),
        )

    # --- visibility ----------------------------------------------------------

    def _require_viewer(self, viewer_id: str | None) -> str:
        if viewer_id is None:
            raise PermissionDeniedError("log in to see the network")
        self._store.node(viewer_id, Person)
        return viewer_id

    def _visible_node(self, viewer: str, node_id: str) -> GraphRecord:
        """The node, if it's a person or org the viewer may see. Anything
        else looks like a missing node, so hidden ones can't be probed."""
        node = self._store.any_node(node_id) if self._store.has_node(node_id) else None
        visible = isinstance(node, (Person, Organization)) and (
            node.is_active and self._privacy.can_view(viewer, node)
        )
        if not visible:
            raise NotFoundError(f"{node_id!r} not found")
        return node  # type: ignore[return-value]

    def _connection_shown(self, viewer: str, edge: Connected) -> bool:
        if viewer in (edge.source_id, edge.target_id):
            return True
        people = [self._store.node(i, Person) for i in (edge.source_id, edge.target_id)]
        if any(p.hide_connections for p in people):
            return False
        return self._privacy.can_view(viewer, edge) and all(
            self._privacy.can_view(viewer, p) for p in people
        )

    def _membership_shown(self, viewer: str, edge: MemberOf) -> bool:
        if not edge.is_confirmed_current:
            return False
        org = self._store.node(edge.target_id, Organization)
        if not (org.is_active and self._privacy.can_view(viewer, org)):
            return False
        if edge.source_id == viewer:
            return True
        member = self._store.node(edge.source_id, Person)
        return self._privacy.can_view(viewer, member) and self._roster.shows(viewer, edge)


class _Walk:
    """One query's view of the graph for one viewer, with neighbors cached."""

    def __init__(self, service: NetworkService, viewer: str) -> None:
        self._service = service
        self._store = service._store
        self._viewer = viewer
        self._cache: dict[str, list[tuple[str, LinkKind]]] = {}

    def neighbors(self, node_id: str) -> list[tuple[str, LinkKind]]:
        if node_id not in self._cache:
            self._cache[node_id] = sorted(set(self._find_neighbors(node_id)))
        return self._cache[node_id]

    def _find_neighbors(self, node_id: str) -> Iterable[tuple[str, LinkKind]]:
        node = self._store.any_node(node_id)
        s, viewer = self._service, self._viewer
        if isinstance(node, Organization):
            for edge in self._store.edges(MemberOf, target_id=node_id):
                if s._membership_shown(viewer, edge):
                    yield edge.source_id, LinkKind.MEMBERSHIP
            return
        for edge in self._store.edges(MemberOf, source_id=node_id):
            if s._membership_shown(viewer, edge):
                yield edge.target_id, LinkKind.MEMBERSHIP
        for edge in self._connections(node_id):
            if edge.is_established and s._connection_shown(viewer, edge):
                other = edge.target_id if edge.source_id == node_id else edge.source_id
                yield other, LinkKind.CONNECTION

    def _connections(self, person_id: str) -> list[Connected]:
        return self._store.edges(Connected, source_id=person_id) + self._store.edges(
            Connected, target_id=person_id
        )

    def node(self, node_id: str) -> NetworkNode:
        record = self._store.any_node(node_id)
        if isinstance(record, Organization):
            return NetworkNode(id=node_id, kind=NodeKind.ORG, label=record.name)
        return NetworkNode(id=node_id, kind=NodeKind.PERSON, label=record.display_name)

    def link(self, a: str, b: str, kind: LinkKind) -> NetworkLink:
        if kind is LinkKind.MEMBERSHIP and isinstance(self._store.any_node(a), Organization):
            a, b = b, a  # memberships always point person -> org
        return NetworkLink(source=a, target=b, kind=kind)

    def links_among(self, node_ids: list[str]) -> tuple[NetworkLink, ...]:
        included = set(node_ids)
        links = {}
        for node_id in node_ids:
            for other, kind in self.neighbors(node_id):
                if other in included:
                    link = self.link(node_id, other, kind)
                    links[(link.source, link.target)] = link
        return tuple(links.values())

    def path(self, start: str, target: str, max_hops: int) -> NetworkPath | None:
        """Fewest links first, then fewest orgs along the way."""
        queue: list[tuple[int, int, tuple[str, ...]]] = [(0, 0, (start,))]
        best: dict[str, tuple[int, int]] = {}
        while queue:
            hops, orgs, route = heapq.heappop(queue)
            current = route[-1]
            if current == target:
                return self._as_path(route)
            if best.get(current, (hops + 1, 0)) <= (hops, orgs) or hops == max_hops:
                continue
            best[current] = (hops, orgs)
            for neighbor, _kind in self.neighbors(current):
                if neighbor in route:
                    continue
                passes_org = neighbor != target and self.is_org(neighbor)
                heapq.heappush(queue, (hops + 1, orgs + passes_org, route + (neighbor,)))
        return None

    def is_org(self, node_id: str) -> bool:
        return isinstance(self._store.any_node(node_id), Organization)

    def _as_path(self, route: tuple[str, ...]) -> NetworkPath:
        kinds = {
            (a, b): dict(self.neighbors(a))[b] for a, b in pairwise(route)
        }
        return NetworkPath(
            nodes=tuple(self.node(i) for i in route),
            links=tuple(self.link(a, b, kind) for (a, b), kind in kinds.items()),
        )
