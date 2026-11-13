"""Graph services that enforce the rules in user_Orgs.md."""

from .errors import NotFoundError, PermissionDeniedError
from .events import EventService
from .hierarchy import ALLOWED_PART_OF, HierarchyService
from .membership import MembershipService
from .privacy import PrivacyService
from .series import SeriesService
from .store import GraphStore, InMemoryGraphStore


class CollectivizeGraph:
    """Facade wiring the store and every rule-enforcing service together."""

    def __init__(self, store: GraphStore | None = None) -> None:
        self.store: GraphStore = store or InMemoryGraphStore()
        self.membership = MembershipService(self.store)
        self.hierarchy = HierarchyService(self.store, self.membership)
        self.events = EventService(self.store, self.membership)
        self.series = SeriesService(self.store, self.membership, self.events)
        self.privacy = PrivacyService(self.store, self.membership, self.events)


__all__ = [
    "ALLOWED_PART_OF",
    "CollectivizeGraph",
    "EventService",
    "GraphStore",
    "HierarchyService",
    "InMemoryGraphStore",
    "MembershipService",
    "NotFoundError",
    "PermissionDeniedError",
    "PrivacyService",
    "SeriesService",
]
