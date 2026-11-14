"""INSTANCE_OF rules and EventSeries operations.

- An event belongs to at most one series.
- Editing the series updates future instances that still inherit the old
  default; past instances are never changed. To change one occurrence,
  revise that event directly via EventService.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from backend.objects import (
    DomainRuleError,
    Event,
    EventSeries,
    EventStatus,
    InstanceOf,
    Provenance,
    ProvenanceMethod,
)

from .errors import PermissionDeniedError
from .events import EventService
from .membership import MembershipService
from .store import GraphStore

EDITABLE_STATUSES = frozenset({EventStatus.DRAFT, EventStatus.PUBLISHED})


class SeriesService:
    def __init__(
        self, store: GraphStore, membership: MembershipService, events: EventService
    ) -> None:
        self._store = store
        self._membership = membership
        self._events = events

    def create(self, series: EventSeries, by_person_id: str) -> EventSeries:
        self._membership.require_admin(by_person_id, series.owner_org_id)
        return self._store.save_node(series)

    def instances(self, series_id: str) -> list[Event]:
        return sorted(
            (
                self._store.node(e.source_id, Event)
                for e in self._store.edges(InstanceOf, target_id=series_id)
            ),
            key=lambda event: event.starts_at,
        )

    def add_instance(
        self, event_id: str, series_id: str, by_person_id: str, now: datetime
    ) -> InstanceOf:
        series = self._store.node(series_id, EventSeries)
        if not self._events.can_edit(by_person_id, event_id):
            raise PermissionDeniedError("only admins of a host org can do that")
        if not self._membership.is_admin(by_person_id, series.owner_org_id):
            raise PermissionDeniedError("only admins of the series' org can add to it")
        if series.owner_org_id not in self._events.host_org_ids(event_id):
            raise DomainRuleError("the series owner must host the event")
        if self._store.edges(InstanceOf, source_id=event_id):
            raise DomainRuleError("an event belongs to at most one series")
        edge = InstanceOf(
            source_id=event_id,
            target_id=series_id,
            provenance=Provenance(
                created_by=by_person_id, method=ProvenanceMethod.ORG_ADMIN, created_at=now
            ),
        )
        return self._store.save_edge(edge)

    def publish_occurrences(
        self,
        series_id: str,
        starts: Sequence[datetime],
        by_person_id: str,
        now: datetime,
    ) -> list[Event]:
        """Publish e.g. a semester of meetings in one action. All or
        nothing: if any occurrence is rejected, none are created."""
        series = self._store.node(series_id, EventSeries)
        self._membership.require_admin(by_person_id, series.owner_org_id)
        published = []
        with self._store.transaction():
            for starts_at in starts:
                provenance = Provenance(
                    created_by=by_person_id,
                    method=ProvenanceMethod.ORG_ADMIN,
                    created_at=now,
                )
                draft = series.instantiate(starts_at, provenance)
                self._events.create(draft, series.owner_org_id, by_person_id)
                self.add_instance(draft.id, series_id, by_person_id, now)
                published.append(self._events.publish(draft.id, by_person_id, now))
        return published

    def update_defaults(
        self, series_id: str, by_person_id: str, now: datetime, **changes: Any
    ) -> EventSeries:
        series = self._store.node(series_id, EventSeries)
        self._membership.require_admin(by_person_id, series.owner_org_id)
        updated = self._store.save_node(series.with_defaults(**changes))
        old_defaults = series.defaults()
        for event in self.instances(series_id):
            if event.starts_at <= now or event.status not in EDITABLE_STATUSES:
                continue
            inherited = {
                name: value
                for name, value in changes.items()
                if getattr(event, name) == old_defaults[name]
            }
            if inherited:
                self._store.save_node(event.revised(now, **inherited))
        return updated
