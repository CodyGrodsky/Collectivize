"""Event and EventSeries nodes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .base import DomainRuleError, GraphRecord, Provenance, require_aware

EDITABLE_FIELDS = frozenset(
    {"title", "description", "starts_at", "ends_at", "location", "capacity"}
)
LOGGED_FIELDS = frozenset({"starts_at", "ends_at", "location"})


class EventStatus(Enum):
    DRAFT = "draft"  # visible only to host admins
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    ENDED = "ended"  # derived once ends_at passes; RSVPs freeze
    ARCHIVED = "archived"  # dropped from discovery, kept for history


@dataclass(frozen=True)
class EventChange:
    """A logged change to time or location, flagged to people who RSVP'd."""

    changed_at: datetime
    field_name: str
    old_value: Any
    new_value: Any


@dataclass(frozen=True, kw_only=True)
class Event(GraphRecord):
    title: str
    starts_at: datetime
    ends_at: datetime
    description: str = ""
    location: str = ""  # free text in MVP; Venue edge Later
    capacity: int | None = None
    is_demo: bool = False  # can never be cleared once set
    status: EventStatus = EventStatus.DRAFT
    change_log: tuple[EventChange, ...] = ()
    embedding: tuple[float, ...] | None = None  # Later

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DomainRuleError("title is required")
        require_aware(self.starts_at, "starts_at")
        require_aware(self.ends_at, "ends_at")
        if self.ends_at <= self.starts_at:
            raise DomainRuleError("ends_at must be after starts_at")
        if self.capacity is not None and self.capacity <= 0:
            raise DomainRuleError("capacity must be positive")

    def status_at(self, now: datetime) -> EventStatus:
        if self.status is EventStatus.PUBLISHED and now >= self.ends_at:
            return EventStatus.ENDED
        return self.status

    def has_ended(self, now: datetime) -> bool:
        return now >= self.ends_at

    def is_discoverable(self, now: datetime) -> bool:
        """Cancelled events stay visible until their original end time."""
        if self.status in (EventStatus.PUBLISHED, EventStatus.CANCELLED):
            return now < self.ends_at
        return False

    def published(self, now: datetime) -> Event:
        if self.status is not EventStatus.DRAFT:
            raise DomainRuleError("only draft events can be published")
        if self.starts_at <= now:
            raise DomainRuleError("an event in the past cannot be published as new")
        return replace(self, status=EventStatus.PUBLISHED)

    def revised(self, now: datetime, **changes: Any) -> Event:
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise DomainRuleError(f"cannot edit fields: {sorted(unknown)}")
        if self.status not in {EventStatus.DRAFT, EventStatus.PUBLISHED}:
            raise DomainRuleError(f"cannot edit a {self.status.value} event")
        if self.has_ended(now):
            raise DomainRuleError("cannot edit an event that has ended")
        log = tuple(
            EventChange(now, name, getattr(self, name), value)
            for name, value in changes.items()
            if name in LOGGED_FIELDS
            and self.status is EventStatus.PUBLISHED
            and getattr(self, name) != value
        )
        return replace(self, change_log=self.change_log + log, **changes)

    def cancelled(self) -> Event:
        if self.status not in {EventStatus.DRAFT, EventStatus.PUBLISHED}:
            raise DomainRuleError(f"cannot cancel a {self.status.value} event")
        return replace(self, status=EventStatus.CANCELLED)

    def archived(self) -> Event:
        return replace(super().archived(), status=EventStatus.ARCHIVED)


@dataclass(frozen=True, kw_only=True)
class EventSeries(GraphRecord):
    """A recurring pattern whose defaults instances inherit unless overridden."""

    owner_org_id: str
    name: str
    recurrence: str  # human description, e.g. "Weekly on Tuesdays"
    default_title: str
    default_duration: timedelta
    default_description: str = ""
    default_location: str = ""
    default_capacity: int | None = None
    is_demo: bool = False

    def __post_init__(self) -> None:
        if self.default_duration <= timedelta(0):
            raise DomainRuleError("default_duration must be positive")

    def defaults(self) -> dict[str, Any]:
        return {
            "title": self.default_title,
            "description": self.default_description,
            "location": self.default_location,
            "capacity": self.default_capacity,
        }

    def instantiate(
        self, starts_at: datetime, provenance: Provenance, **overrides: Any
    ) -> Event:
        fields = {**self.defaults(), **overrides}
        return Event(
            provenance=provenance,
            starts_at=starts_at,
            ends_at=starts_at + self.default_duration,
            is_demo=self.is_demo,
            visibility=self.visibility,
            **fields,
        )

    def with_defaults(self, **changes: Any) -> EventSeries:
        mapping = {
            "title": "default_title",
            "description": "default_description",
            "location": "default_location",
            "capacity": "default_capacity",
        }
        unknown = set(changes) - set(mapping)
        if unknown:
            raise DomainRuleError(f"cannot change series defaults: {sorted(unknown)}")
        return replace(self, **{mapping[k]: v for k, v in changes.items()})

