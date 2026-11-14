"""Finding people, organizations and events by name.

One search box, three kinds of result. Each kind follows the rules that
already govern it, so searching never shows more than browsing would:

- People: only people the viewer may see (`PrivacyService.can_view`), so
  logged-out visitors find nobody and a hidden person looks missing.
  Matched on their name only, never on their profile text or email.
- Organizations: only orgs the viewer may see, matched on name.
- Events: `DiscoveryService.search` with the text as a filter, so the
  same visibility, listing and ranking rules apply as on Discover.

Matching ignores case and accents-as-typed (NFKC); every word typed must
appear in the name. Names that start with the text come first. A search
needs at least MIN_QUERY_LENGTH characters, and each kind returns at most
`limit` results (with the total found), so the directory can't be paged
through wholesale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.objects import DomainRuleError, Organization, Person

from .connections import ConnectionService
from .discovery import DiscoveryItem, DiscoveryQuery, DiscoveryService, normalize_text
from .privacy import PrivacyService
from .store import GraphStore

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 8
MAX_LIMIT = 25
DEMO_PREFIX = "[Demo] "
DEMO_TAG = " (demo"


@dataclass(frozen=True)
class FoundPerson:
    person: Person
    connected: bool  # an accepted connection of the viewer


@dataclass(frozen=True)
class Found:
    """Up to `limit` matches of each kind, best first, with how many matched."""

    text: str
    people: tuple[FoundPerson, ...]
    orgs: tuple[Organization, ...]
    events: tuple[DiscoveryItem, ...]
    people_total: int
    orgs_total: int
    events_total: int


class DirectoryService:
    def __init__(
        self,
        store: GraphStore,
        privacy: PrivacyService,
        connections: ConnectionService,
        discovery: DiscoveryService,
    ) -> None:
        self._store = store
        self._privacy = privacy
        self._connections = connections
        self._discovery = discovery

    def find(
        self, viewer_id: str | None, text: str, now: datetime, *, limit: int = DEFAULT_LIMIT
    ) -> Found:
        """People, orgs and events whose name matches `text`, for this viewer."""
        if not 1 <= limit <= MAX_LIMIT:
            raise DomainRuleError(f"limit must be between 1 and {MAX_LIMIT}")
        words = normalize_text(text).split()
        query = " ".join(words)
        if not MIN_QUERY_LENGTH <= len(query) <= MAX_QUERY_LENGTH:
            raise DomainRuleError(
                f"search for between {MIN_QUERY_LENGTH} and {MAX_QUERY_LENGTH} characters"
            )
        people = self._people(viewer_id, words, query)
        orgs = self._orgs(viewer_id, words, query)
        events = self._discovery.search(
            DiscoveryQuery(viewer_id=viewer_id, text=query, limit=limit), now
        )
        return Found(
            text=query,
            people=tuple(people[:limit]),
            orgs=tuple(orgs[:limit]),
            events=events.items,
            people_total=len(people),
            orgs_total=len(orgs),
            events_total=events.total,
        )

    def _people(self, viewer_id: str | None, words: list[str], query: str) -> list[FoundPerson]:
        if viewer_id is None:
            return []  # people are only visible to logged-in users
        friends = set(self._connections.connections_of(viewer_id))
        ranked = []
        for person in self._store.nodes(Person):
            name = searchable_name(person.display_name)
            if person.id == viewer_id or not person.is_active or not _matches(name, words):
                continue
            if not self._privacy.can_view(viewer_id, person):
                continue
            connected = person.id in friends
            # Your own connections first, then the best name match.
            ranked.append(((not connected, _rank(name, query), name, person.id),
                           FoundPerson(person=person, connected=connected)))
        return [found for _, found in sorted(ranked, key=lambda pair: pair[0])]

    def _orgs(self, viewer_id: str | None, words: list[str], query: str) -> list[Organization]:
        ranked = []
        for org in self._store.nodes(Organization):
            name = searchable_name(org.name)
            if org.is_active and _matches(name, words) and self._privacy.can_view(viewer_id, org):
                ranked.append(((_rank(name, query), name, org.id), org))
        return [org for _, org in sorted(ranked, key=lambda pair: pair[0])]


def searchable_name(name: str) -> str:
    """A name as people would type it: normalized, and without the demo
    data's "[Demo]" prefix and "(demo role)" tag, so searching "demo" or
    "student" doesn't match every demo record."""
    return normalize_text(name.removeprefix(DEMO_PREFIX).split(DEMO_TAG, 1)[0]).strip()


def _matches(name: str, words: list[str]) -> bool:
    return all(word in name for word in words)


def _rank(name: str, query: str) -> int:
    """0: the name starts with the text. 1: one of its words does. 2: elsewhere."""
    if name.startswith(query):
        return 0
    if any(word.startswith(query.split()[0]) for word in name.split()):
        return 1
    return 2
