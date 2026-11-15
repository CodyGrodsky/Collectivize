"""Account management, independent of HTTP (server.py wraps it).

Two sides, kept apart on purpose:

- **Your own account** (`my_*`, `update_my_settings`, `sign_out_everywhere`,
  `delete_my_account`): the acting person is always the signed-in session's
  person, passed in by the server, never read from the request.
- **Platform admins** (`admin_*`): Collectivize staff managing any account:
  read it in full, edit it, create one, sign it out, delete it, and grant
  or remove the platform-admin role.

Rules every admin method follows:

1. The admin check runs first, inside the method, against the stored
   account (never a flag from the request), so it can't be skipped by
   calling the method another way.
2. Every input is validated before anything is written, and each change
   is all-or-nothing.
3. Every change is written to the audit log: who, what, which account and
   which *fields*, never the values.
4. Guard rails: deleting needs the account's email typed back; admins use
   their own account page to delete themselves; nobody changes their own
   admin role; the last platform admin can't be removed.
5. Admins can't switch on someone's consent settings (`ai_uses_history`,
   `analytics_consent`): those belong to the person alone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from http import HTTPStatus
from typing import Any

from backend.app_api import (
    EMAIL_SHAPE,
    MAX_EMAIL_LENGTH,
    _id,
    _name_from_email,
    _person,
)
from backend.audit import AuditLog
from backend.config import ALLOWED_EMAIL_DOMAINS
from backend.demo_api import ApiError, GraphFactory
from backend.graph import CollectivizeGraph
from backend.network_api import AUDIENCE_NAMES, profile_changes
from backend.objects import (
    MemberOf,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    Rsvp,
    VerificationLevel,
)
from backend.objects.base import utc_now
from backend.objects.person import MAX_DISPLAY_NAME
from backend.sessions import SessionStore

PROFILE_KEYS = frozenset({
    "display_name", "bio", "about", "website", "top3", "colors", "sections",
    "profile_visibility", "rsvp_visibility", "hide_connections",
})
ADMIN_KEYS = PROFILE_KEYS | {"person_id", "email", "is_platform_admin"}
CONSENT_KEYS = ("ai_uses_history", "analytics_consent")


class AccountsApi:
    def __init__(
        self,
        graphs: GraphFactory,
        sessions: SessionStore,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._graphs = graphs
        self._sessions = sessions
        self._clock = clock
        self.audit = audit or AuditLog()

    # --- your own account ----------------------------------------------------------

    def my_account(self, person_id: str) -> dict[str, Any]:
        """Everything the account holds about you that isn't on your profile
        page: sign-in email, settings, and how much is attached to it."""
        with self._graphs() as graph:
            return self._account_json(graph, _person(graph, person_id))

    def update_my_settings(self, person_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Your consent settings: `ai_uses_history` (AI search may read your
        history for your own results) and `analytics_consent` (your data may
        be in pseudonymized datasets). Only you can change them."""
        changes = {key: body[key] for key in CONSENT_KEYS if key in body}
        unknown = sorted(set(body) - set(CONSENT_KEYS))
        if unknown or not changes:
            raise ApiError(HTTPStatus.BAD_REQUEST, "send ai_uses_history and/or analytics_consent")
        if not all(isinstance(value, bool) for value in changes.values()):
            raise ApiError(HTTPStatus.BAD_REQUEST, "settings must be true or false")
        with self._graphs() as graph:
            saved = graph.store.save_node(replace(_person(graph, person_id), **changes))
            return self._account_json(graph, saved)

    def sign_out_everywhere(self, person_id: str) -> dict[str, Any]:
        """End every session of yours, on every browser, including this one."""
        return {"signed_out": True, "sessions_ended": self._sessions.end_all(person_id)}

    def delete_my_account(self, person_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """True erasure of your own account (see PrivacyService.erase_person).
        `confirm` must be your sign-in email. Not undoable."""
        with self._graphs() as graph:
            person = _person(graph, person_id)
            _require_confirmation(person, body.get("confirm"))
            self._keep_an_admin(graph, person)
            graph.privacy.erase_person(person.id)
        self._sessions.end_all(person_id)
        return {"deleted": True}

    # --- platform admins -----------------------------------------------------------

    def admin_account(self, admin_id: str, params: dict[str, str]) -> dict[str, Any]:
        """One account in full, for platform admins."""
        with self._graphs() as graph:
            _require_admin(graph, admin_id)
            person = _person(graph, _id(params.get("id"), "person"))
            return self._account_json(graph, person)

    def admin_update(self, admin_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit an account: `person_id` plus any profile field (as
        POST /api/profile), `email`, or `is_platform_admin`."""
        with self._graphs() as graph:
            _require_admin(graph, admin_id)
            unknown = sorted(set(body) - ADMIN_KEYS)
            if unknown:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"cannot change: {', '.join(unknown)[:200]}")
            try:
                changes = profile_changes(body)
            except ValueError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
            person = _person(graph, _id(body.get("person_id"), "person"))
            account: dict[str, Any] = {}
            if "email" in body:
                account["email"] = _new_email(graph, body["email"], keep=person.id)
            if "is_platform_admin" in body:
                account["is_platform_admin"] = self._new_role(
                    graph, admin_id, person, body["is_platform_admin"]
                )
            account = {k: v for k, v in account.items() if getattr(person, k) != v}
            if not changes and not account:
                return self._account_json(graph, person)
            with graph.store.transaction():
                if changes:
                    person = graph.profiles.update(person.id, admin_id, **changes)
                if account:
                    person = graph.store.save_node(replace(person, **account))
            self.audit.record(admin_id, "update", person.id, self._clock(),
                              ", ".join(sorted({*changes, *account})))
            if account.get("is_platform_admin") is False:
                self._sessions.end_all(person.id)  # an ex-admin starts over
            return self._account_json(graph, person)

    def admin_create(self, admin_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create an account for `email` (optionally with `display_name`)."""
        with self._graphs() as graph:
            _require_admin(graph, admin_id)
            name = body.get("display_name")
            if name is not None and (
                not isinstance(name, str) or not 0 < len(name.strip()) <= MAX_DISPLAY_NAME
            ):
                raise ApiError(HTTPStatus.BAD_REQUEST,
                               f"display name must be 1 to {MAX_DISPLAY_NAME} characters")
            email = _new_email(graph, body.get("email"), keep=None)
            person = graph.store.save_node(Person(
                display_name=name.strip() if name else _name_from_email(email),
                email=email,
                verification_level=VerificationLevel.UNIVERSITY_EMAIL,
                provenance=Provenance(
                    created_by=admin_id, method=ProvenanceMethod.STAFF, created_at=self._clock()
                ),
            ))
            self.audit.record(admin_id, "create", person.id, self._clock())
            return self._account_json(graph, person)

    def admin_sign_out(self, admin_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """End every session of an account (e.g. after a report)."""
        with self._graphs() as graph:
            _require_admin(graph, admin_id)
            person = _person(graph, _id(body.get("person_id"), "person"))
        ended = self._sessions.end_all(person.id)
        self.audit.record(admin_id, "sign-out", person.id, self._clock())
        return {"person_id": person.id, "sessions_ended": ended}

    def admin_delete(self, admin_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """True erasure of someone's account. `confirm` must be that
        account's sign-in email. Not undoable."""
        with self._graphs() as graph:
            _require_admin(graph, admin_id)
            person = _person(graph, _id(body.get("person_id"), "person"))
            if person.id == admin_id:
                raise ApiError(HTTPStatus.BAD_REQUEST,
                               "delete your own account from your Account page")
            _require_confirmation(person, body.get("confirm"))
            self._keep_an_admin(graph, person)
            graph.privacy.erase_person(person.id)
        self._sessions.end_all(person.id)
        self.audit.record(admin_id, "delete", person.id, self._clock())
        return {"deleted": True, "person_id": person.id}

    # --- internals -----------------------------------------------------------------

    def _new_role(
        self, graph: CollectivizeGraph, admin_id: str, person: Person, value: Any
    ) -> bool:
        if not isinstance(value, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "is_platform_admin must be true or false")
        if value != person.is_platform_admin:
            if person.id == admin_id:
                raise ApiError(HTTPStatus.BAD_REQUEST, "you can't change your own admin role")
            if not value:
                self._keep_an_admin(graph, person)
        return value

    @staticmethod
    def _keep_an_admin(graph: CollectivizeGraph, leaving: Person) -> None:
        if not leaving.is_platform_admin:
            return
        if not any(p.is_platform_admin and p.id != leaving.id for p in graph.store.nodes(Person)):
            raise ApiError(HTTPStatus.BAD_REQUEST,
                           "this is the last platform admin: make someone else one first")

    def _account_json(self, graph: CollectivizeGraph, person: Person) -> dict[str, Any]:
        memberships = graph.store.edges(MemberOf, source_id=person.id)
        return {
            "id": person.id,
            "name": person.display_name,
            "email": person.email,
            "verification": person.verification_level.value,
            "created_at": person.provenance.created_at.isoformat(),
            "is_platform_admin": person.is_platform_admin,
            "profile": {
                "bio": person.bio,
                "about": person.profile,
                "website": person.website,
            },
            "settings": {
                "profile_visibility": AUDIENCE_NAMES.get(person.profile_visibility),
                "rsvp_visibility": AUDIENCE_NAMES.get(person.rsvp_visibility),
                "hide_connections": person.hide_connections,
                "ai_uses_history": person.ai_uses_history,
                "analytics_consent": person.analytics_consent,
            },
            "counts": {
                "connections": len(graph.connections.connections_of(person.id)),
                "orgs": len(memberships),
                "rsvps": len(graph.store.edges(Rsvp, source_id=person.id)),
            },
            "orgs": [
                {"id": edge.target_id, "role": edge.role.value,
                 "name": graph.store.node(edge.target_id, Organization).name}
                for edge in memberships
            ],
            "sessions": self._sessions.count_for(person.id),
        }


def _require_admin(graph: CollectivizeGraph, person_id: str) -> None:
    if not graph.visibility.is_platform_admin(person_id):
        raise ApiError(HTTPStatus.FORBIDDEN, "only platform admins can do that")


def _require_confirmation(person: Person, confirm: Any) -> None:
    """Deleting needs the account's email (or its id, if it has none) typed back."""
    expected = person.email or person.id
    if not isinstance(confirm, str) or confirm.strip().lower() != expected.lower():
        raise ApiError(HTTPStatus.BAD_REQUEST,
                       "type the account's email exactly to confirm deleting it")


def _new_email(graph: CollectivizeGraph, value: Any, *, keep: str | None) -> str:
    """A valid, allowed sign-in email that no other account (than `keep`) uses."""
    if not isinstance(value, str) or len(value) > MAX_EMAIL_LENGTH:
        raise ApiError(HTTPStatus.BAD_REQUEST, "enter an email address")
    address = value.strip().lower()
    match = EMAIL_SHAPE.match(address)
    if match is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "enter a valid email address")
    if match.group(1) not in ALLOWED_EMAIL_DOMAINS:
        raise ApiError(HTTPStatus.BAD_REQUEST,
                       f"accounts need an @{' or @'.join(ALLOWED_EMAIL_DOMAINS)} email")
    if any(p.email == address and p.id != keep for p in graph.store.nodes(Person)):
        raise ApiError(HTTPStatus.CONFLICT, "another account already uses that email")
    return address
