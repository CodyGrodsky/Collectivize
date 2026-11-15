"""Small rules shared by the account methods (accounts.py): what a valid
sign-in email is, the confirmation needed to delete an account, and the
notices sent when a sign-in email changes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from backend.app_api import EMAIL_SHAPE, MAX_EMAIL_LENGTH, canonical_email
from backend.config import ALLOWED_EMAIL_DOMAINS
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.objects import Person

CHANGED_BY_YOU = (
    "The sign-in email for your Collectivize account was just changed, and this address no "
    "longer signs in to it.\n\nIf that wasn't you, contact the people who run Collectivize.\n"
)
CHANGED_BY_STAFF = (
    "The people who run Collectivize changed the sign-in email for your account, and this "
    "address no longer signs in to it.\n\nIf you didn't ask for that, contact them.\n"
)


def change_purpose(person_id: str) -> str:
    return f"email-change:{person_id}"


def valid_email(value: Any) -> str:
    """A canonical, allowed Northeastern address (taken or not)."""
    if not isinstance(value, str) or len(value) > MAX_EMAIL_LENGTH:
        raise ApiError(HTTPStatus.BAD_REQUEST, "enter an email address")
    address = canonical_email(value)
    match = EMAIL_SHAPE.match(address)
    if match is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "enter a valid email address")
    if match.group(1) not in ALLOWED_EMAIL_DOMAINS:
        raise ApiError(HTTPStatus.BAD_REQUEST,
                       f"accounts need an @{' or @'.join(ALLOWED_EMAIL_DOMAINS)} email")
    return address


def require_confirmation(person: Person, confirm: Any) -> None:
    """Deleting needs the account's email (or its id, if it has none) typed back."""
    expected = person.email or person.id
    if not isinstance(confirm, str) or confirm.strip().lower() != expected.lower():
        raise ApiError(HTTPStatus.BAD_REQUEST,
                       "type the account's email exactly to confirm deleting it")


def new_email(graph: CollectivizeGraph, value: Any, *, keep: str | None) -> str:
    """A valid, allowed sign-in email that no other account (than `keep`) uses."""
    address = valid_email(value)  # one mailbox, one account
    if any(p.email == address and p.id != keep for p in graph.store.nodes(Person)):
        raise ApiError(HTTPStatus.CONFLICT, "another account already uses that email")
    return address
