"""Who among the platform admins may do what.

Three levels:

- **Owner**: manages the admins. Has every power, and is the only one who
  can make someone an admin, remove an admin, change an admin's powers,
  change a sign-in email, or touch another admin's account. Who the owner
  is comes from the local credentials file (`role: "owner"`, set with
  `scripts/set_admin_password.py --owner`), never from the app, so no
  admin can promote themselves.
- **Platform admin**: can open /admin, see the list of accounts and the
  log, plus whichever of ADMIN_POWERS the owner has given them.
- **Everyone else**: nothing here.

Every check reads the stored account at the time of the request, so a
changed power or a removed admin takes effect on their very next click.
"""

from __future__ import annotations

from http import HTTPStatus

from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.objects import Person
from backend.objects.person import ADMIN_POWERS

VIEW_INFO, VIEW_AS, EDIT, CREATE, SIGN_OUT, DELETE = ADMIN_POWERS


class AdminPolicy:
    def __init__(self, owners: frozenset[str] | None = None) -> None:
        """`owners`: the sign-in emails of the owner accounts. None means
        owners aren't configured (tests, old callers): every platform admin
        then counts as an owner, as before powers existed."""
        self._owners = owners

    @property
    def owners_are_set(self) -> bool:
        return self._owners is not None

    def is_owner(self, person: Person) -> bool:
        if not person.is_platform_admin:
            return False
        return self._owners is None or (person.email or "") in self._owners

    def powers(self, person: Person) -> frozenset[str]:
        if self.is_owner(person):
            return frozenset(ADMIN_POWERS)
        return frozenset(person.admin_powers) if person.is_platform_admin else frozenset()

    def admin(self, graph: CollectivizeGraph, person_id: str) -> Person:
        """The acting admin, or 403."""
        node = graph.store.any_node(person_id) if graph.store.has_node(person_id) else None
        if not isinstance(node, Person) or not node.is_platform_admin:
            raise ApiError(HTTPStatus.FORBIDDEN, "only platform admins can do that")
        return node

    def require(self, graph: CollectivizeGraph, person_id: str, power: str) -> Person:
        admin = self.admin(graph, person_id)
        if power not in self.powers(admin):
            raise ApiError(HTTPStatus.FORBIDDEN, "your admin account isn't allowed to do that")
        return admin

    def require_owner(self, graph: CollectivizeGraph, person_id: str) -> Person:
        admin = self.admin(graph, person_id)
        if not self.is_owner(admin):
            raise ApiError(HTTPStatus.FORBIDDEN, "only the owner can do that")
        return admin

    def check_target(self, admin: Person, target: Person) -> None:
        """Admin accounts are the owner's to manage: no other admin may
        read, edit, sign out, view as or delete one (their own included)."""
        if target.is_platform_admin and not self.is_owner(admin):
            raise ApiError(HTTPStatus.FORBIDDEN, "only the owner can manage admin accounts")
