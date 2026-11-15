"""Setting up an admin from the command line (scripts/set_admin_password.py):
making sure the account an admin login opens exists and is a platform
admin, and the rule that each login has an account of its own."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.admin_auth import AdminAuth, AdminAuthError
from backend.app_api import AppApi, _name_from_email
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.objects import Person, Provenance, ProvenanceMethod, VerificationLevel


def own_account_email(auth: AdminAuth, username: str, email: str) -> str:
    """The canonical address for `username`'s login, or AdminAuthError if it
    isn't a Northeastern address or another login already opens that
    account (the admin log could then not tell the two people apart)."""
    try:
        address = AppApi.check_email(email)
    except ApiError as error:
        raise AdminAuthError(error.message) from None
    for other in auth.usernames_for(address):
        if other.casefold() != username.strip().casefold():
            raise AdminAuthError(
                f"the login '{other}' already opens {address}. Each admin needs their own "
                "account: give this login the admin's own Northeastern address"
            )
    return address


def ensure_admin_account(graph: CollectivizeGraph, address: str, now: datetime) -> tuple[Person, str]:
    """The platform-admin account for this address, created or promoted if
    needed. Returns it and what was done: "created", "promoted" or "ready"."""
    person = next((p for p in graph.store.nodes(Person) if p.email == address), None)
    if person is None:
        person = graph.store.save_node(Person(
            display_name=_name_from_email(address),
            email=address,
            verification_level=VerificationLevel.UNIVERSITY_EMAIL,
            is_platform_admin=True,
            provenance=Provenance(created_by=None, method=ProvenanceMethod.STAFF, created_at=now),
        ))
        return person, "created"
    if not person.is_platform_admin:
        return graph.store.save_node(replace(person, is_platform_admin=True)), "promoted"
    return person, "ready"
