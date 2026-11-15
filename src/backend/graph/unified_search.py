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
- Keyword fit (text_match.py): the share of keywords found, weighted by
  where they were found (a name counts more than a description). With
  keywords, only results that match at least one are listed, and results
  that match every keyword come before those that match only some.
- People: fit, then graph proximity (1st > 2nd > 3rd+ degree), so search
  connects you to the closest people for what you're after. Degrees and
  paths are never returned.
- Events: fit and the Discover likelihood (ranking.py).
- Organizations: fit, then ones your connections or you belong to.
- With no keywords and no type filter, the list is Discover's events.

When a search would come back empty or useless, it is widened one step at
a time, and the page says what was done:
1. a keyword found in more than half of everything ("demo" in the demo
   data) is left out when another keyword narrows things down;
2. nothing matched: one wrong letter per word is allowed ("robtics");
3. still nothing, and the text itself named a kind ("people who like
   hiking"): every kind is searched;
4. still nothing, and it was a sentence: its other filters alone.

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

from .discovery import DiscoveryItem, DiscoveryQuery, DiscoveryService
from .privacy import PrivacyService
from .query_understanding import TYPES, Extraction, QueryReader, Understanding, words_of
from .store import GraphStore
from .text_match import Fit, KeywordMatcher
from .visibility import VisibilityService

PER_PAGE = 10
FLOOD_SHARE = 0.5  # a keyword found in more than this share of everything says nothing
PROXIMITY = {0: 0.0, 1: 1.0, 2: 0.6, 3: 0.3}
NAME_WEIGHT, TAG_WEIGHT, TEXT_WEIGHT = 1.0, 0.8, 0.6


@dataclass(frozen=True)
class SearchFilters:
    """What the person ticked. Empty means "not set"."""

    types: frozenset[str] = frozenset()
    tag_slugs: tuple[str, ...] = ()
    day: date | None = None
    days: int = 1  # how many days from `day` ("this weekend" is two)


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
    full: bool = True  # every keyword was found (listed before partial matches)


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
        read = understanding.extraction
        applied = _merge(filters, read, tags)
        keywords = _keywords(read)
        tag_ids = tuple(tags[s].id for s in applied.tag_slugs if s in tags)
        notes = [understanding.note] if understanding.note else []

        def gather(words: list[str], using: SearchFilters, fuzzy: bool = False):
            matcher = KeywordMatcher(words, fuzzy=fuzzy)
            return self._gather(viewer_id, matcher, using, tag_ids, now), matcher

        results, matcher = gather(keywords, applied)
        common = matcher.flooding(FLOOD_SHARE)
        if common:
            # "alice demo" here: "demo" is in nearly every name, so it says
            # nothing. Search on the words that do narrow things down.
            keywords = [k for k in keywords if k not in common]
            results, matcher = gather(keywords, applied)
            notes.append(f"Left out {_quoted(common)}: it matches almost everything here.")
        if not results and matcher.can_be_fuzzy:
            # Nothing matched as typed: allow one wrong letter per word.
            results, matcher = gather(keywords, applied, fuzzy=True)
            if results and matcher.corrections:
                notes.append(f"Nothing matched {_quoted(matcher.corrections)}, so this shows "
                             f"results for {_quoted(matcher.corrections.values())}.")
        if not results and keywords and read.types and not filters.types:
            # The text named a kind ("people who like hiking") and nothing of
            # that kind matched: look at every kind instead.
            wide = replace(applied, types=frozenset())
            results, matcher = gather(keywords, wide)
            if not results and matcher.can_be_fuzzy:
                results, matcher = gather(keywords, wide, fuzzy=True)
            if results:
                kinds = " or ".join(sorted(read.types))
                notes.append(f"No {kinds} matched {_quoted(keywords)}, so this searched everything.")
                applied = wide
        if not results and keywords and understanding.mode == "sentence":
            # A request like "show me something rad": its words name nothing
            # here, so answer with what the rest of it asked for (any type,
            # topic or day it gave) instead of an empty page.
            results, matcher = gather([], applied)
            if results:
                notes.append(f"Nothing matched “{', '.join(keywords)}”, so here's what's coming up.")
                keywords = []
                read = replace(read, summary="")
        understanding = replace(understanding, note=" ".join(notes),
                                extraction=replace(read, keywords=tuple(keywords)))

        # A search for something lists what's already in your network first,
        # then everything else; each part keeps its best-first order.
        # Plain Discover (nothing typed or ticked) stays one ranked list.
        grouped = bool(keywords or applied.types or applied.tag_slugs or applied.day)
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
        matcher: KeywordMatcher,
        applied: SearchFilters,
        tag_ids: tuple[str, ...],
        now: datetime,
    ) -> list[SearchResult]:
        """Everything that matches: results with every keyword first, then
        best first."""
        types = set(applied.types) or set(TYPES)
        if not matcher.keywords and not applied.types:
            types = {"events"}  # nothing asked for: Discover's ranked events
        if applied.day is not None or applied.tag_slugs:
            types.discard("people")  # people have no date or public topics
        if applied.day is not None:
            types.discard("orgs")
        results: list[SearchResult] = []
        if "people" in types:
            results += self._people(viewer_id, matcher)
        if "orgs" in types:
            results += self._orgs(viewer_id, matcher, set(tag_ids))
        if "events" in types:
            results += self._events(viewer_id, matcher, tag_ids, applied, now)
        results.sort(key=lambda r: (not r.full, -r.score, _sort_name(r)))
        return results

    def _people(self, viewer_id: str, matcher: KeywordMatcher) -> list[SearchResult]:
        keywords = matcher.keywords
        degrees = self._visibility.distances(viewer_id)
        found = []
        for person in self._store.nodes(Person):
            if person.id == viewer_id or not person.is_active:
                continue
            if not self._privacy.can_view(viewer_id, person):
                continue
            fit, full = 1.0, True
            if keywords:
                top3 = " ".join((person.top3_title, *person.top3_items))
                fit, full = matcher.fit([(person.display_name, NAME_WEIGHT),
                                         (person.bio, TEXT_WEIGHT), (top3, TEXT_WEIGHT)])
                if fit == 0:
                    continue
            closeness = PROXIMITY.get(degrees.get(person.id, -1), 0.0)
            if not keywords and closeness == 0:
                continue  # browsing people: only those within three hops
            score = 0.65 * fit + 0.35 * closeness if keywords else closeness
            found.append(SearchResult("person", person.id, score, node=person,
                                      connected=degrees.get(person.id) == 1, full=full))
        return found

    def _orgs(self, viewer_id: str, matcher: KeywordMatcher, tag_ids: set[str]) -> list[SearchResult]:
        keywords = matcher.keywords
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
            fit, full = 1.0, True
            if keywords:
                fit, full = matcher.fit([
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
            found.append(SearchResult("org", org.id, score, node=org, connected=org.id in mine,
                                      full=full))
        return found

    def _events(
        self,
        viewer_id: str,
        matcher: KeywordMatcher,
        tag_ids: tuple[str, ...],
        applied: SearchFilters,
        now: datetime,
    ) -> list[SearchResult]:
        keywords = matcher.keywords
        after = before = None
        if applied.day is not None:
            zone = ZoneInfo(CAMPUS_TIMEZONE)
            after = datetime.combine(applied.day, time.min, zone)
            before = datetime.combine(applied.day + timedelta(days=applied.days), time.min, zone)
        labels = {t.id: t.label for t in self._store.nodes(Tag)}
        mine = set(self._visibility.org_ids_of(viewer_id))
        fits: dict[str, Fit] = {}

        def matches(event: Event, host: Organization | Person, tag_ids: set[str]) -> bool:
            """Checked before ranking, so only events that match are ranked."""
            host_name = host.name if isinstance(host, Organization) else host.display_name
            fits[event.id] = matcher.fit([
                (event.title, NAME_WEIGHT),
                (" ".join(labels.get(t, "") for t in tag_ids), TAG_WEIGHT),
                (host_name, TAG_WEIGHT),
                (event.description, TEXT_WEIGHT), (event.location, TEXT_WEIGHT),
            ])
            return fits[event.id].score > 0

        items = self._discovery.rank_all(
            DiscoveryQuery(viewer_id=viewer_id, tag_ids=tag_ids,
                           starts_after=after, starts_before=before), now,
            keep=matches if keywords else None,
        )
        found = []
        for item in items:
            fit, full = fits.get(item.event.id, Fit(1.0, True))
            score = 0.6 * fit + 0.4 * item.score if keywords else item.score
            if item.is_cancelled:
                score -= 1.0  # cancelled events always sink to the bottom
            connected = (item.my_rsvp in (RsvpStatus.GOING, RsvpStatus.INTERESTED)
                         or item.host.id == viewer_id or item.host.id in mine)
            found.append(SearchResult("event", item.event.id, score, event=item,
                                      connected=connected, full=full))
        return found


# --- helpers -------------------------------------------------------------------------


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
        # A ticked day that is the one the text gave keeps the text's span,
        # so page two of "this weekend" is still both days.
        days=read.days if filters.day in (None, read.day) and read.day else 1,
    )


def _quoted(words: Any) -> str:
    return ", ".join(f"“{w}”" for w in words)


def _sort_name(result: SearchResult) -> Any:
    if result.event is not None:
        return (result.event.event.starts_at.isoformat(), result.id)
    node = result.node
    name = node.display_name if isinstance(node, Person) else getattr(node, "name", "")
    return (name.casefold(), result.id)
