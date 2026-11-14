"""Edges of the Collectivize graph.

Each edge is directed `source_id -> target_id` and carries its own
provenance, visibility and lifecycle (from GraphRecord). Person-owned
edges also carry a recommendation-consent flag, separate from visibility.

Every edge class declares which kinds of node it may start and end at
(`SOURCE_TYPES` / `TARGET_TYPES`). Stores call `check_endpoints` before
saving, so a membership can only run Person -> Org, a connection only
Person <-> Person, and so on.

Deliberately NOT edges: similarity between people and predicted
attendance. Those are computed at ranking time and never stored as facts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import ClassVar

from .base import DomainRuleError, GraphRecord, Visibility, require_aware
from .event import Event, EventSeries
from .organization import Organization
from .person import Person
from .taxonomy import Tag

NodeTypes = tuple[type[GraphRecord], ...]


class RecommendationUse(Enum):
    """Whether a person-owned fact may feed recommendations."""

    NONE = "none"
    OWN_ONLY = "own_only"  # default: improve only this person's recs
    NETWORK = "network"  # may also place the person in front of others


@dataclass(frozen=True, kw_only=True)
class Edge(GraphRecord):
    SOURCE_TYPES: ClassVar[NodeTypes] = ()
    TARGET_TYPES: ClassVar[NodeTypes] = ()

    source_id: str
    target_id: str

    def __post_init__(self) -> None:
        if self.source_id == self.target_id:
            raise DomainRuleError("an edge cannot point to its own source")

    @classmethod
    def check_endpoints(cls, source: GraphRecord, target: GraphRecord) -> None:
        """Raise unless this kind of edge may connect these kinds of node."""
        for end, node, allowed in (
            ("start", source, cls.SOURCE_TYPES),
            ("end", target, cls.TARGET_TYPES),
        ):
            if not isinstance(node, allowed):
                names = " or ".join(t.__name__ for t in allowed) or "nothing"
                raise DomainRuleError(
                    f"{cls.__name__} must {end} at a {names}, not a {type(node).__name__}"
                )


@dataclass(frozen=True, kw_only=True)
class PersonOwnedEdge(Edge):
    recommendation_use: RecommendationUse = RecommendationUse.OWN_ONLY

    def with_recommendation_use(self, use: RecommendationUse) -> PersonOwnedEdge:
        return replace(self, recommendation_use=use)


# --- Person -> Org -----------------------------------------------------------


class MemberRole(Enum):
    MEMBER = "member"
    OFFICER = "officer"
    ADMIN = "admin"


@dataclass(frozen=True, kw_only=True)
class MemberOf(PersonOwnedEdge):
    """Person (source) belongs to Org (target). Two-sided: the person
    claims it, the org confirms it. `confirmed_by` records who confirmed
    it (an org admin, or staff approving a claim); never the person."""

    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Organization,)

    role: MemberRole = MemberRole.MEMBER
    started_at: datetime | None = None
    ended_at: datetime | None = None
    org_confirmed: bool = False
    confirmed_by: str | None = None
    visibility: Visibility = Visibility.MEMBERS

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.started_at, "started_at")
        require_aware(self.ended_at, "ended_at")
        if self.org_confirmed and not self.confirmed_by:
            raise DomainRuleError("a confirmed membership must record who confirmed it")
        if self.confirmed_by == self.source_id:
            raise DomainRuleError("nobody can confirm their own membership")

    @property
    def is_current(self) -> bool:
        return self.is_active and self.ended_at is None

    @property
    def is_confirmed_current(self) -> bool:
        return self.is_current and self.org_confirmed

    @property
    def is_admin(self) -> bool:
        return self.is_confirmed_current and self.role is MemberRole.ADMIN

    def confirmed(self, by: str) -> MemberOf:
        return replace(self, org_confirmed=True, confirmed_by=by)

    def with_role(self, role: MemberRole) -> MemberOf:
        return replace(self, role=role)

    def ended(self, at: datetime) -> MemberOf:
        return replace(self, ended_at=at)


@dataclass(frozen=True, kw_only=True)
class Follows(PersonOwnedEdge):
    """Later. One-sided "keep me updated"; no approval, private by default."""

    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Organization,)

    visibility: Visibility = Visibility.PRIVATE


# --- Org -> Org --------------------------------------------------------------


class PartOfKind(Enum):
    CHAPTER_OF = "chapter_of"  # chapter -> national
    MEMBER_OF_COUNCIL = "member_of_council"  # chapter/club -> council
    UNDER_INSTITUTION = "under_institution"  # anything -> institution


@dataclass(frozen=True, kw_only=True)
class PartOf(Edge):
    """Child org (source) is structurally part of parent org (target)."""

    SOURCE_TYPES = (Organization,)
    TARGET_TYPES = (Organization,)

    kind: PartOfKind
    child_accepted: bool = False
    parent_accepted: bool = False
    staff_verified: bool = False

    @property
    def is_established(self) -> bool:
        both_accepted = self.child_accepted and self.parent_accepted
        return self.is_active and (both_accepted or self.staff_verified)


@dataclass(frozen=True, kw_only=True)
class PartnersWith(Edge):
    """Later. Peer (non-hierarchical) relation; needs both confirmations."""

    SOURCE_TYPES = (Organization,)
    TARGET_TYPES = (Organization,)

    source_confirmed: bool = False
    target_confirmed: bool = False

    @property
    def is_established(self) -> bool:
        return self.is_active and self.source_confirmed and self.target_confirmed


# --- Org/Person -> Event, Event -> Series -------------------------------------------


class HostRole(Enum):
    PRIMARY = "primary"
    CO_HOST = "co_host"


class HostOrigin(Enum):
    """How a co-host link started, which decides who must approve it."""

    INVITED = "invited"  # a host invited this party; the party approves
    REQUESTED = "requested"  # this party asked to join; a host approves


@dataclass(frozen=True, kw_only=True)
class Hosts(Edge):
    """Org or Person (source) is responsible for Event (target). An event
    has one primary host and any number of co-hosts, people or orgs. A
    co-host counts only once the other side approves: the invited party
    for an invitation, or a current host for a request to join."""

    SOURCE_TYPES = (Organization, Person)
    TARGET_TYPES = (Event,)

    role: HostRole
    accepted: bool = False
    origin: HostOrigin = HostOrigin.INVITED

    @property
    def is_effective(self) -> bool:
        return self.is_active and self.accepted


@dataclass(frozen=True, kw_only=True)
class InstanceOf(Edge):
    """Event (source) is one occurrence of EventSeries (target)."""

    SOURCE_TYPES = (Event,)
    TARGET_TYPES = (EventSeries,)


# --- Person -> Event (Later) -------------------------------------------------


class RsvpStatus(Enum):
    GOING = "going"
    INTERESTED = "interested"
    NOT_GOING = "not_going"


@dataclass(frozen=True, kw_only=True)
class Rsvp(PersonOwnedEdge):
    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Event,)

    status: RsvpStatus
    updated_at: datetime


class AttendanceMethod(Enum):
    SELF_REPORTED = "self_reported"
    HOST_CHECK_IN = "host_check_in"
    TICKET_SCAN = "ticket_scan"


@dataclass(frozen=True, kw_only=True)
class Attended(PersonOwnedEdge):
    """Strongest signal; private by default (location history is sensitive)."""

    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Event,)

    method: AttendanceMethod
    visibility: Visibility = Visibility.PRIVATE


# --- Person <-> Person (Later) -----------------------------------------------


class ConnectionOrigin(Enum):
    REQUEST = "request"  # one person asked, the other accepted
    INVITE = "invite"  # the inviter's invite, redeemed by the new person


@dataclass(frozen=True, kw_only=True)
class Connected(PersonOwnedEdge):
    """Mutual connection; exists only when both people accept. The source
    is whoever sent the request. Visible to logged-in users by default;
    either person can hide their whole list (`Person.hide_connections`),
    and a connection shows only if neither person has."""

    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Person,)

    source_accepted: bool = False
    target_accepted: bool = False
    origin: ConnectionOrigin = ConnectionOrigin.REQUEST
    visibility: Visibility = Visibility.NETWORK

    @property
    def is_established(self) -> bool:
        return self.is_active and self.source_accepted and self.target_accepted


# --- Tagging -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Tagged(Edge):
    """Org or Event (source) is tagged with Tag (target) by its admins."""

    SOURCE_TYPES = (Organization, Event)
    TARGET_TYPES = (Tag,)


@dataclass(frozen=True, kw_only=True)
class InterestedIn(PersonOwnedEdge):
    """Later. Person (source) -> Tag (target). Set only by the person."""

    SOURCE_TYPES = (Person,)
    TARGET_TYPES = (Tag,)

    visibility: Visibility = Visibility.PRIVATE
