"""Who can see what about whom (v1.1 spec 4.1): one module every screen uses.

The rules depend only on how the viewer is connected to the subject:

- degree: 0 (self), 1 (connected), 2 (a connection's connection), 3 (three
  or more hops) or None (no path). Breadth-first, capped at 3 hops.
- Network: you may open your own and a 1st-degree connection's, never
  further (one hop only). The network map itself is NetworkService's.
- Org membership can't be hidden: `org_ids_of` ignores a membership's own
  visibility setting.
- Profile sections: you and your connections see everything; anyone else
  signed in sees the partial card; a preview card (on a connection request
  or invite) is the card and nothing below it. The profile page itself is
  ProfileService's.
- Org member lists: an org's members see the whole list; everyone else sees
  the count plus the members they're connected to.

The API applies these before anything leaves the server, so hidden data is
never sent to the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.objects import MemberOf, Organization, Person

from .connections import ConnectionService
from .membership import MembershipService
from .store import GraphStore

MAX_DEGREE = 3  # "3" means three or more


class Card(Enum):
    FULL = "full"  # name, bio, Top 3 and the rest of the card
    PARTIAL = "partial"  # name, bio, Top 3 (spec 7.3 default)


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
            return ProfileSections(Card.FULL, orgs=False, events=False, network=False)
        if self.can_view_network(viewer_id, subject_id):
            return ProfileSections(Card.FULL, orgs=True, events=True, network=True)
        return ProfileSections(Card.PARTIAL, orgs=False, events=False, network=False)

    def org_ids_of(self, person_id: str) -> list[str]:
        """Confirmed, current memberships. These can't be hidden (spec 4.2
        rule 7), so the membership's own visibility setting is not used."""
        return [
            m.target_id
            for m in self._store.edges(MemberOf, source_id=person_id)
            if m.is_confirmed_current
        ]

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
