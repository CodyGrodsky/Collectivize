"""Unified search (v1.1 spec 4.4 / 4.5): people, organizations and events
in one ranked list, ten to a page.

What a query asks for comes from query_understanding.py (keywords, types,
topics, a day). Filters the person ticked win over what was read from the
text; the rest of what was read fills the gaps.

Filters:
- types: any of people / orgs / events; none ticked means all three.
- topics: events and orgs tagged with one of them (people have no public
  topics, so a topic filter leaves people out).
- day: events starting that day, campus time (people and orgs have no
  date, so a day filter leaves them out).

Ranking (scores are used to order results, never shown to users):
- Keyword fit: the share of keywords found, weighted by where they were
  found (a name counts more than a description). With keywords, only
  results that match at least one are listed.
- People: fit, then graph proximity (1st > 2nd > 3rd+ degree), so search
  connects you to the closest people for what you're after. Degrees and
  paths are never returned.
- Events: fit and the Discover likelihood (ranking.py).
- Organizations: fit, then ones your connections or you belong to.
- With no keywords and no type filter, the list is Discover's events.

Privacy: people are matched only on what every signed-in user may see on
their card (name, bio, Top 3), never on orgs or events, so search can't
reveal anything the profile wouldn't. Events go through Discover's
visibility rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.config import CAMPUS_TIMEZONE
from backend.objects import (
    Event,
    MemberOf,
    Organization,
    Person,
    RsvpStatus,
    Tag,
    Tagged,
)

from .discovery import DiscoveryItem, DiscoveryQuery, DiscoveryService, normalize_text
from .privacy import PrivacyService
from .query_understanding import TYPES, Extraction, QueryReader, Understanding, words_of
from .store import GraphStore
from .visibility import VisibilityService

PER_PAGE = 10
STEM_PREFIX = 4  # "robot" matches "robotics"
PROXIMITY = {0: 0.0, 1: 1.0, 2: 0.6, 3: 0.3}
NAME_WEIGHT, TAG_WEIGHT, TEXT_WEIGHT = 1.0, 0.8, 0.6


@dataclass(frozen=True)
class SearchFilters:
    """What the person ticked. Empty means "not set"."""

    types: frozenset[str] = frozenset()
    tag_slugs: tuple[str, ...] = ()
    day: date | None = None


@dataclass(frozen=True)
class SearchResult:
    kind: str  # "person", "org" or "event"
    id: str
    score: float  # for ordering (and the platform admin's debug view) only
    node: Person | Organization | None = None
    event: DiscoveryItem | None = None
    # Already part of the viewer's network: a person they're connected to,
    # an org they belong to, or an event they're going to (or interested
    # in), host themselves, or that one of their orgs hosts.
    connected: bool = False


@dataclass(frozen=True)
class SearchPage:
    results: tuple[SearchResult, ...]
    total: int
    page: int
    pages: int
    understanding: Understanding
    applied: SearchFilters  # the filters actually used, ticked or read
    counts: dict[str, int] = field(default_factory=dict)  # results per type, all pages
    grouped: bool = False  # True when `connected` results are listed first


class UnifiedSearchService:
    def __init__(
        self,
        store: GraphStore,
        discovery: DiscoveryService,
        visibility: VisibilityService,
        privacy: PrivacyService,
        reader: QueryReader,
    ) -> None:
        self._store = store
        self._discovery = discovery
        self._visibility = visibility
        self._privacy = privacy
        self._reader = reader

    def search(
        self,
        viewer_id: str,
        text: str,
        filters: SearchFilters,
        now: datetime,
        page: int = 1,
        per_page: int = PER_PAGE,
    ) -> SearchPage:
        tags = {t.slug: t for t in self._store.nodes(Tag)}
        today = now.astimezone(ZoneInfo(CAMPUS_TIMEZONE)).date()
        understanding = self._reader.read(text, today, tuple(t.label for t in tags.values()))
        applied = _merge(filters, understanding.extraction, tags)
        keywords = _keywords(understanding.extraction)
        tag_ids = tuple(tags[s].id for s in applied.tag_slugs if s in tags)

        results = self._gather(viewer_id, keywords, applied, tag_ids, now)
        if not results and keywords and understanding.mode == "sentence":
            # A request like "show me something rad": its words name nothing
            # here, so answer with what the rest of it asked for (any type,
            # topic or day it gave) instead of an empty page.
            results = self._gather(viewer_id, [], applied, tag_ids, now)
            if results:
                missed = ", ".join(keywords)
                understanding = replace(
                    understanding,
                    extraction=replace(understanding.extraction, keywords=(), summary=""),
                    note=f"Nothing matched “{missed}”, so here's what's coming up.",
                )

        # A search for something lists what's already in your network first,
        # then everything else; each part keeps its best-first order.
        # Plain Discover (nothing typed or ticked) stays one ranked list.
        grouped = bool(_keywords(understanding.extraction) or applied.types
                       or applied.tag_slugs or applied.day)
        if grouped:
            results.sort(key=lambda r: not r.connected)  # stable

        total = len(results)
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        window = results[(page - 1) * per_page : page * per_page]
        counts = {kind: sum(r.kind == kind for r in results) for kind in ("person", "org", "event")}
        return SearchPage(tuple(window), total, page, pages, understanding, applied, counts, grouped)

    # --- each kind ----------------------------------------------------------------

    def _gather(
        self,
        viewer_id: str,
        keywords: list[str],
        applied: SearchFilters,
        tag_ids: tuple[str, ...],
        now: datetime,
    ) -> list[SearchResult]:
        """Everything that matches, best first."""
        types = set(applied.types) or set(TYPES)
        if not keywords and not applied.types:
            types = {"events"}  # nothing asked for: Discover's ranked events
        if applied.day is not None or applied.tag_slugs:
            types.discard("people")  # people have no date or public topics
        if applied.day is not None:
            types.discard("orgs")
        results: list[SearchResult] = []
        if "people" in types:
            results += self._people(viewer_id, keywords)
        if "orgs" in types:
            results += self._orgs(viewer_id, keywords, set(tag_ids))
        if "events" in types:
            results += self._events(viewer_id, keywords, tag_ids, applied.day, now)
        results.sort(key=lambda r: (-r.score, _sort_name(r)))
        return results

    def _people(self, viewer_id: str, keywords: list[str]) -> list[SearchResult]:
        degrees = self._visibility.distances(viewer_id)
        found = []
        for person in self._store.nodes(Person):
            if person.id == viewer_id or not person.is_active:
                continue
            if not self._privacy.can_view(viewer_id, person):
                continue
            fit = 1.0
            if keywords:
                top3 = " ".join((person.top3_title, *person.top3_items))
                fit = keyword_fit(keywords, [(person.display_name, NAME_WEIGHT),
                                             (person.bio, TEXT_WEIGHT), (top3, TEXT_WEIGHT)])
                if fit == 0:
                    continue
            closeness = PROXIMITY.get(degrees.get(person.id, -1), 0.0)
            if not keywords and closeness == 0:
                continue  # browsing people: only those within three hops
            score = 0.65 * fit + 0.35 * closeness if keywords else closeness
            found.append(SearchResult("person", person.id, score, node=person,
                                      connected=degrees.get(person.id) == 1))
        return found

    def _orgs(self, viewer_id: str, keywords: list[str], tag_ids: set[str]) -> list[SearchResult]:
        labels = {t.id: t.label for t in self._store.nodes(Tag)}
        mine = set(self._visibility.org_ids_of(viewer_id))
        friends = set(self._visibility.distances(viewer_id, 1)) - {viewer_id}
        found = []
        for org in self._store.nodes(Organization):
            if not org.is_active or not self._privacy.can_view(viewer_id, org):
                continue
            org_tags = {e.target_id for e in self._store.edges(Tagged, source_id=org.id)}
            if tag_ids and not org_tags & tag_ids:
                continue
            fit = 1.0
            if keywords:
                fit = keyword_fit(keywords, [
                    (org.name, NAME_WEIGHT),
                    (" ".join(labels.get(t, "") for t in org_tags), TAG_WEIGHT),
                    (org.kind.value, TEXT_WEIGHT),
                ])
                if fit == 0:
                    continue
            members = {m.source_id for m in self._store.edges(MemberOf, target_id=org.id)
                       if m.is_confirmed_current}
            social = 1.0 if org.id in mine else min(1.0, len(members & friends) / 3)
            score = 0.8 * fit + 0.2 * social if keywords else 0.5 + 0.5 * social
            found.append(SearchResult("org", org.id, score, node=org, connected=org.id in mine))
        return found

    def _events(
        self,
        viewer_id: str,
        keywords: list[str],
        tag_ids: tuple[str, ...],
        day: date | None,
        now: datetime,
    ) -> list[SearchResult]:
        after = before = None
        if day is not None:
            zone = ZoneInfo(CAMPUS_TIMEZONE)
            after = datetime.combine(day, time.min, zone)
            before = after + timedelta(days=1)
        labels = {t.id: t.label for t in self._store.nodes(Tag)}
        mine = set(self._visibility.org_ids_of(viewer_id))
        fits: dict[str, float] = {}

        def matches(event: Event, host: Organization | Person, tag_ids: set[str]) -> bool:
            """Checked before ranking, so only events that match are ranked."""
            host_name = host.name if isinstance(host, Organization) else host.display_name
            fits[event.id] = keyword_fit(keywords, [
                (event.title, NAME_WEIGHT),
                (" ".join(labels.get(t, "") for t in tag_ids), TAG_WEIGHT),
                (host_name, TAG_WEIGHT),
                (event.description, TEXT_WEIGHT), (event.location, TEXT_WEIGHT),
            ])
            return fits[event.id] > 0

        items = self._discovery.rank_all(
            DiscoveryQuery(viewer_id=viewer_id, tag_ids=tag_ids,
                           starts_after=after, starts_before=before), now,
            keep=matches if keywords else None,
        )
        found = []
        for item in items:
            fit = fits.get(item.event.id, 1.0)
            score = 0.6 * fit + 0.4 * item.score if keywords else item.score
            if item.is_cancelled:
                score -= 1.0  # cancelled events always sink to the bottom
            connected = (item.my_rsvp in (RsvpStatus.GOING, RsvpStatus.INTERESTED)
                         or item.host.id == viewer_id or item.host.id in mine)
            found.append(SearchResult("event", item.event.id, score, event=item, connected=connected))
        return found


# --- matching ------------------------------------------------------------------------


def keyword_fit(keywords: list[str], fields: list[tuple[str, float]]) -> float:
    """0..1: each keyword scores the weight of the best field it's found
    in (on a shared stem, so "robot" finds "robotics"), averaged over the
    keywords. A keyword phrase found whole in a name counts fully."""
    if not keywords:
        return 1.0
    words = [(set(words_of(text)), normalize_text(text), weight) for text, weight in fields if text]
    total = 0.0
    for keyword in keywords:
        parts = words_of(keyword)
        best = 0.0
        for present, raw, weight in words:
            if len(parts) > 1 and normalize_text(keyword) in raw:
                best = max(best, weight)
            elif parts and all(any(_same_stem(p, w) for w in present) for p in parts):
                best = max(best, weight)
        total += best
    return total / len(keywords)


def _same_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= STEM_PREFIX and longer.startswith(shorter)


def _keywords(extraction: Extraction) -> list[str]:
    return [k for k in extraction.keywords if words_of(k)]


def _merge(filters: SearchFilters, read: Extraction, tags: dict[str, Tag]) -> SearchFilters:
    """Ticked filters win; what was read from the text fills the rest."""
    by_label = {t.label.casefold(): slug for slug, t in tags.items()}
    read_slugs = tuple(by_label[label.casefold()] for label in read.tags if label.casefold() in by_label)
    return SearchFilters(
        types=filters.types or read.types,
        tag_slugs=filters.tag_slugs or read_slugs,
        day=filters.day or read.day,
    )


def _sort_name(result: SearchResult) -> Any:
    if result.event is not None:
        return (result.event.event.starts_at.isoformat(), result.id)
    node = result.node
    name = node.display_name if isinstance(node, Person) else getattr(node, "name", "")
    return (name.casefold(), result.id)
