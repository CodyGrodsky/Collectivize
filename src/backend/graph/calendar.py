"""A host's calendar: every event a person or org is hosting.

A combined event appears on the calendar of every host that accepted it,
and disappears from a co-host's calendar as soon as that co-host leaves.
Pending invitations never appear.

Results are split into `public` (visible to anyone) and `private`
(members-only, connections-only, private, or never published, including
drafts cancelled before publishing). The viewer only ever gets events
they are allowed to see, and a host whose page the viewer can't see has
no visible calendar at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.objects import Event, EventStatus, Hosts, Visibility

from .errors import PermissionDeniedError
from .events import EventService
from .privacy import PrivacyService
from .store import GraphStore

HIDDEN_STATUSES = frozenset({EventStatus.ARCHIVED})


@dataclass(frozen=True)
class HostEvents:
    host_id: str
    public: tuple[Event, ...]
    private: tuple[Event, ...]


class CalendarService:
    def __init__(
        self, store: GraphStore, events: EventService, privacy: PrivacyService
    ) -> None:
        self._store = store
        self._events = events
        self._privacy = privacy

    def host_events(
        self,
        host_id: str,
        viewer_id: str | None,
        *,
        starts_after: datetime | None = None,
        starts_before: datetime | None = None,
    ) -> HostEvents:
        host = self._events.host_node(host_id)
        if not self._privacy.can_view(viewer_id, host):
            raise PermissionDeniedError("you can't see this calendar")
        visible = sorted(
            (
                event
                for event in self._hosted_events(host_id)
                if _in_window(event, starts_after, starts_before)
                and self._privacy.can_view(viewer_id, event)
            ),
            key=lambda e: (e.starts_at, e.id),
        )
        return HostEvents(
            host_id=host_id,
            public=tuple(e for e in visible if not _is_private(e)),
            private=tuple(e for e in visible if _is_private(e)),
        )

    def _hosted_events(self, host_id: str) -> list[Event]:
        events = (
            self._store.node(edge.target_id, Event)
            for edge in self._store.edges(Hosts, source_id=host_id)
            if edge.is_effective
        )
        return [e for e in events if e.is_active and e.status not in HIDDEN_STATUSES]


def _is_private(event: Event) -> bool:
    return event.visibility is not Visibility.PUBLIC or not event.was_published


def _in_window(event: Event, after: datetime | None, before: datetime | None) -> bool:
    """Whether the event overlaps the window, so one already under way at
    `after` still shows."""
    if after is not None and event.ends_at <= after:
        return False
    return before is None or event.starts_at < before
