"""Cross-cutting rules every node and edge follows (see user_Orgs.md).

- Provenance: who created a record, how, when it was last confirmed, and
  its verification status.
- Visibility: an ordered audience level; an edge's effective visibility is
  the most restrictive of its own level and both endpoints'.
- Lifecycle: records are soft-removed or archived first; only account
  erasure hard-deletes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum, IntEnum
from typing import Self


class DomainRuleError(ValueError):
    """Raised when an operation would violate a rule of the graph."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def require_aware(value: datetime | None, name: str) -> None:
    if value is not None and value.tzinfo is None:
        raise DomainRuleError(f"{name} must be timezone-aware")


class Visibility(IntEnum):
    """Audience levels, ordered from least to most restrictive."""

    PUBLIC = 0
    NETWORK = 1  # any logged-in user
    MEMBERS = 2  # confirmed members of the related org(s)
    CONNECTIONS = 3  # the person's mutual connections
    PRIVATE = 4  # only the owner


def most_restrictive(*levels: Visibility) -> Visibility:
    return max(levels)


class VerificationStatus(Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class ProvenanceMethod(Enum):
    ORG_ADMIN = "org_admin"  # submitted by an org admin
    EXTERNAL_IMPORT = "external_import"  # imported from an external calendar/site
    STAFF = "staff"  # entered by a Collectivize staffer
    SELF_CLAIM = "self_claim"  # self-claimed by a person


class RecordState(Enum):
    ACTIVE = "active"
    REMOVED = "removed"
    ARCHIVED = "archived"


@dataclass(frozen=True, kw_only=True)
class Provenance:
    created_by: str | None
    method: ProvenanceMethod
    created_at: datetime = field(default_factory=utc_now)
    last_confirmed_at: datetime | None = None
    verification: VerificationStatus = VerificationStatus.UNVERIFIED

    def __post_init__(self) -> None:
        require_aware(self.created_at, "created_at")
        require_aware(self.last_confirmed_at, "last_confirmed_at")

    @property
    def last_known_good(self) -> datetime:
        return self.last_confirmed_at or self.created_at

    def confirmed(self, at: datetime) -> Provenance:
        return replace(self, last_confirmed_at=at)

    def with_verification(self, status: VerificationStatus) -> Provenance:
        return replace(self, verification=status)

    def is_stale(self, now: datetime, max_age: timedelta) -> bool:
        return now - self.last_known_good > max_age


@dataclass(frozen=True, kw_only=True)
class GraphRecord:
    """Base for every node and edge. Instances are immutable; use the
    `with_*` / state methods to derive updated copies."""

    provenance: Provenance
    id: str = field(default_factory=new_id)
    visibility: Visibility = Visibility.PUBLIC
    state: RecordState = RecordState.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.state is RecordState.ACTIVE

    def with_visibility(self, visibility: Visibility) -> Self:
        return replace(self, visibility=visibility)

    def with_provenance(self, provenance: Provenance) -> Self:
        return replace(self, provenance=provenance)

    def removed(self) -> Self:
        return replace(self, state=RecordState.REMOVED)

    def archived(self) -> Self:
        return replace(self, state=RecordState.ARCHIVED)
