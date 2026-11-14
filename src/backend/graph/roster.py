"""Org member lists, governed by one policy object.

Who may see member lists is a product decision that is expected to
change, so it lives in a single place: `RosterPolicy`. Change
`DEFAULT_ROSTER_POLICY` (or pass a policy to `CollectivizeGraph`) and
every member list follows.

Current decision: member lists are public. Two rules still hold whatever
the policy says:
- Only confirmed, current members are listed (pending requests and past
  members are not).
- A list is never more visible than the org itself.
With `hide_private_memberships` on, anyone who set their own membership
or profile to private is left off the list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.objects import (
    DomainRuleError,
    MemberOf,
    MemberRole,
    Organization,
    Person,
    Visibility,
    most_restrictive,
)

from .errors import PermissionDeniedError
from .membership import MembershipService
from .store import GraphStore

ROSTER_AUDIENCES = frozenset(
    {Visibility.PUBLIC, Visibility.NETWORK, Visibility.MEMBERS, Visibility.PRIVATE}
)
ROLE_ORDER = {MemberRole.ADMIN: 0, MemberRole.OFFICER: 1, MemberRole.MEMBER: 2}


@dataclass(frozen=True, kw_only=True)
class RosterPolicy:
    """Who may see an org's member list.

    audience: PUBLIC (anyone), NETWORK (logged-in users), MEMBERS (the
    org's confirmed members), or PRIVATE (the org's admins only).
    """

    audience: Visibility = Visibility.PUBLIC
    hide_private_memberships: bool = True

    def __post_init__(self) -> None:
        if self.audience not in ROSTER_AUDIENCES:
            raise DomainRuleError(f"{self.audience.name} is not a member-list audience")


DEFAULT_ROSTER_POLICY = RosterPolicy()


@dataclass(frozen=True)
class RosterEntry:
    person: Person
    role: MemberRole
    since: datetime | None


class RosterService:
    def __init__(
        self,
        store: GraphStore,
        membership: MembershipService,
        policy: RosterPolicy = DEFAULT_ROSTER_POLICY,
    ) -> None:
        self._store = store
        self._membership = membership
        self.policy = policy

    def can_see(self, viewer_id: str | None, org_id: str) -> bool:
        org = self._store.node(org_id, Organization)
        level = most_restrictive(self.policy.audience, org.visibility)
        if level is Visibility.PUBLIC:
            return True
        if viewer_id is None:
            return False
        if level is Visibility.NETWORK:
            return True
        if level is Visibility.MEMBERS:
            return self._membership.is_member(viewer_id, org_id)
        return self._membership.is_admin(viewer_id, org_id)

    def members(self, org_id: str, viewer_id: str | None) -> tuple[RosterEntry, ...]:
        if not self.can_see(viewer_id, org_id):
            raise PermissionDeniedError("you can't see this org's members")
        entries = [
            RosterEntry(person=person, role=edge.role, since=edge.started_at)
            for edge in self._store.edges(MemberOf, target_id=org_id)
            if edge.is_confirmed_current
            and (person := self._store.node(edge.source_id, Person))
            and not self._hidden(edge, person)
        ]
        return tuple(
            sorted(entries, key=lambda e: (ROLE_ORDER[e.role], e.person.display_name))
        )

    def _hidden(self, edge: MemberOf, person: Person) -> bool:
        if not self.policy.hide_private_memberships:
            return False
        return Visibility.PRIVATE in (edge.visibility, person.visibility)
