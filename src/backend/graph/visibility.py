"""Who can see what about whom (v1.1 spec 4.1): one module every screen uses.

The rules depend only on how the viewer is connected to the subject:

- degree: 0 (self), 1 (connected), 2 (a connection's connection), 3 (three
  or more hops) or None (no path). Breadth-first, capped at 3 hops.
- Network: you see your own (your connections and your orgs), and a 1st-
  degree connection's (their connections and orgs, i.e. your 2nd degree).
  Never further: one hop only. Non-connections' networks are not shown.
- Org membership can't be hidden: a membership appears wherever its
  member's network is visible.
- Profile: you and your connections see everything; anyone else signed in
  sees the partial card (photo, name, bio, Top 3); a preview card (on a
  connection request or invite) is the full card and nothing below it.
- Org member lists: an org's members see the whole list; everyone else sees
  the count plus the members they're connected to.

The API applies these before anything leaves the server, so hidden data is
never sent to the browser.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from backend.objects import MemberOf, Organization, Person

from .connections import ConnectionService
from .membership import MembershipService
from .store import GraphStore

MAX_DEGREE = 3  # "3" means three or more
NETWORK_PEOPLE_CAP = 25  # SPEC-ASSUMPTION: the spec's suggested default cap
NETWORK_ORG_CAP = 10


class Card(Enum):
    FULL = "full"  # photo, name, bio, pictures, Top 3
    PARTIAL = "partial"  # photo, name, bio, Top 3 (spec 7.3 default)


class ConnectionState(Enum):
    SELF = "self"
    CONNECTED = "connected"
    PENDING_OUT = "pending_out"  # the viewer asked; waiting on them
    PENDING_IN = "pending_in"  # they asked the viewer
    NONE = "none"


@dataclass(frozen=True)
class ProfileSections:
    card: Card
    orgs: bool
    events: bool
    network: bool
    pictures: bool  # the extra pictures on the card (not in the partial card)


@dataclass(frozen=True)
class NetworkNode:
    id: str
    kind: str  # "person" or "org"
    label: str
    is_center: bool = False
    is_viewer: bool = False
    # Already the viewer's 1st-degree connection (marked in a friend's network).
    viewer_connected: bool = False
    connection: ConnectionState = ConnectionState.NONE


@dataclass(frozen=True)
class NetworkView:
    center: NetworkNode
    people: tuple[NetworkNode, ...]
    orgs: tuple[NetworkNode, ...]
    links: tuple[tuple[str, str], ...]  # center -> each shown node
    people_total: int
    orgs_total: int
    connections_hidden: bool = False  # the subject turned off showing connections

    @property
    def has_more(self) -> bool:
        return len(self.people) < self.people_total or len(self.orgs) < self.orgs_total


@dataclass(frozen=True)
class MemberList:
    count: int
    member_ids: tuple[str, ...]  # who this viewer may see by name
    is_full: bool  # True when every member is listed


class VisibilityService:
    def __init__(
        self,
        store: GraphStore,
        connections: ConnectionService,
        membership: MembershipService,
    ) -> None:
        self._store = store
        self._connections = connections
        self._membership = membership

    # --- distance ------------------------------------------------------------

    def distances(self, viewer_id: str, max_degree: int = MAX_DEGREE) -> dict[str, int]:
        """Degree of everyone within `max_degree` hops of the viewer (the
        viewer is 0). Anyone missing is further away or unreachable."""
        seen = {viewer_id: 0}
        frontier = [viewer_id]
        for depth in range(1, max_degree + 1):
            following: list[str] = []
            for person_id in frontier:
                for other in self._connections.connections_of(person_id):
                    if other not in seen:
                        seen[other] = depth
                        following.append(other)
            frontier = following
            if not frontier:
                break
        return seen

    def degree(self, viewer_id: str, user_id: str) -> int | None:
        """0 (self), 1, 2, 3 (three or more) or None (no path)."""
        if viewer_id == user_id:
            return 0
        if self._connections.are_connected(viewer_id, user_id):
            return 1
        found = self.distances(viewer_id, MAX_DEGREE - 1).get(user_id)
        if found is not None:
            return found
        # Anything beyond two hops is "3+": check that some path exists.
        return MAX_DEGREE if self._reachable(viewer_id, user_id) else None

    def connection_state(self, viewer_id: str, subject_id: str) -> ConnectionState:
        if viewer_id == subject_id:
            return ConnectionState.SELF
        edge = self._connections.between(viewer_id, subject_id)
        if edge is None:
            return ConnectionState.NONE
        if edge.is_established:
            return ConnectionState.CONNECTED
        return (
            ConnectionState.PENDING_OUT
            if edge.source_id == viewer_id
            else ConnectionState.PENDING_IN
        )

    # --- profiles and networks -----------------------------------------------

    def can_view_network(self, viewer_id: str, subject_id: str) -> bool:
        """Only your own network or a 1st-degree connection's."""
        return viewer_id == subject_id or self._connections.are_connected(
            viewer_id, subject_id
        )

    def visible_profile_sections(
        self, viewer_id: str, subject_id: str, *, preview: bool = False
    ) -> ProfileSections:
        if preview:  # the card shown with a connection request or invite
            return ProfileSections(Card.FULL, orgs=False, events=False, network=False,
                                   pictures=True)
        if self.can_view_network(viewer_id, subject_id):
            return ProfileSections(Card.FULL, orgs=True, events=True, network=True,
                                   pictures=True)
        return ProfileSections(Card.PARTIAL, orgs=False, events=False, network=False,
                               pictures=False)

    def org_ids_of(self, person_id: str) -> list[str]:
        """Confirmed, current memberships. These can't be hidden (spec 4.2
        rule 7), so the membership's own visibility setting is not used."""
        return [
            m.target_id
            for m in self._store.edges(MemberOf, source_id=person_id)
            if m.is_confirmed_current
        ]

    def visible_network(
        self,
        viewer_id: str,
        subject_id: str,
        *,
        people_limit: int = NETWORK_PEOPLE_CAP,
        org_limit: int = NETWORK_ORG_CAP,
    ) -> NetworkView | None:
        """The subject's network as the viewer may see it, or None when the
        viewer may not see it at all. Never the whole network: at most
        `people_limit` people and `org_limit` orgs (ask again with higher
        limits for "Show more")."""
        if not self.can_view_network(viewer_id, subject_id):
            return None
        subject = self._store.node(subject_id, Person)
        is_own = viewer_id == subject_id
        mine = set(self._connections.connections_of(viewer_id))
        hidden = subject.hide_connections and not is_own
        people_ids = [] if hidden else self._connections.connections_of(subject_id)
        people_ids = self._order_people(people_ids, mine, viewer_id, is_own)
        org_ids = sorted(self.org_ids_of(subject_id), key=lambda i: self._org_name(i))

        def person(pid: str) -> NetworkNode:
            return NetworkNode(
                id=pid, kind="person", label=self._store.node(pid, Person).display_name,
                is_viewer=pid == viewer_id,
                viewer_connected=pid in mine,
                connection=self.connection_state(viewer_id, pid),
            )

        center = NetworkNode(
            id=subject_id, kind="person", label=subject.display_name, is_center=True,
            is_viewer=is_own, viewer_connected=subject_id in mine,
            connection=self.connection_state(viewer_id, subject_id),
        )
        people = tuple(person(pid) for pid in people_ids[: max(0, people_limit)])
        orgs = tuple(
            NetworkNode(id=oid, kind="org", label=self._org_name(oid))
            for oid in org_ids[: max(0, org_limit)]
        )
        return NetworkView(
            center=center,
            people=people,
            orgs=orgs,
            links=tuple((subject_id, n.id) for n in people + orgs),
            people_total=len(people_ids),
            orgs_total=len(org_ids),
            connections_hidden=hidden,
        )

    # --- organizations -------------------------------------------------------

    def visible_members(self, viewer_id: str, org_id: str) -> MemberList:
        """Members see the whole list; anyone else sees the count and the
        members they're connected to (spec 7.1, decided by the team)."""
        self._store.node(org_id, Organization)
        member_ids = sorted(
            {m.source_id for m in self._store.edges(MemberOf, target_id=org_id)
             if m.is_confirmed_current},
            key=lambda i: self._store.node(i, Person).display_name.casefold(),
        )
        if self._membership.is_member(viewer_id, org_id):
            return MemberList(len(member_ids), tuple(member_ids), is_full=True)
        mine = set(self._connections.connections_of(viewer_id))
        shown = tuple(i for i in member_ids if i in mine)
        return MemberList(len(member_ids), shown, is_full=len(shown) == len(member_ids))

    def can_manage_org(self, viewer_id: str, org_id: str) -> bool:
        """Org admins edit their org's page and add or remove its members."""
        return self._membership.is_admin(viewer_id, org_id)

    # --- platform admin ------------------------------------------------------

    def is_platform_admin(self, person_id: str) -> bool:
        if not self._store.has_node(person_id):
            return False
        node = self._store.any_node(person_id)
        return isinstance(node, Person) and node.is_platform_admin

    # --- internals -----------------------------------------------------------

    def _reachable(self, a: str, b: str) -> bool:
        seen = {a}
        frontier = [a]
        while frontier:
            following = []
            for person_id in frontier:
                for other in self._connections.connections_of(person_id):
                    if other == b:
                        return True
                    if other not in seen:
                        seen.add(other)
                        following.append(other)
            frontier = following
        return False

    def _order_people(
        self, ids: Iterable[str], mine: set[str], viewer_id: str, is_own: bool
    ) -> list[str]:
        """In a friend's network: you, then people you already know, then
        the rest. In your own: the people you share the most friends with
        first. Ties by name, so the order is stable."""
        def name(pid: str) -> str:
            return self._store.node(pid, Person).display_name.casefold()

        ids = list(ids)
        if is_own:
            shared = {pid: len(mine & set(self._connections.connections_of(pid))) for pid in ids}
            return sorted(ids, key=lambda pid: (-shared[pid], name(pid)))
        return sorted(ids, key=lambda pid: (pid != viewer_id, pid not in mine, name(pid)))

    def _org_name(self, org_id: str) -> str:
        return self._store.node(org_id, Organization).name
