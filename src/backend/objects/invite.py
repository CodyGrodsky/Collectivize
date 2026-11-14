"""Invite node: a single-use, expiring invitation from one person.

The invite's id is a SHA-256 fingerprint of its code. The code itself is
shown to the inviter once and never stored, so someone who reads the
database still can't use an open invite.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .base import GraphRecord, Visibility, require_aware


@dataclass(frozen=True, kw_only=True)
class Invite(GraphRecord):
    inviter_id: str
    expires_at: datetime
    used_by: str | None = None
    used_at: datetime | None = None
    revoked: bool = False
    visibility: Visibility = Visibility.PRIVATE

    def __post_init__(self) -> None:
        require_aware(self.expires_at, "expires_at")
        require_aware(self.used_at, "used_at")

    def is_open(self, now: datetime) -> bool:
        return (
            self.is_active
            and not self.revoked
            and self.used_by is None
            and now < self.expires_at
        )

    def used(self, person_id: str, now: datetime) -> Invite:
        return replace(self, used_by=person_id, used_at=now)

    def revoked_now(self) -> Invite:
        return replace(self, revoked=True)
