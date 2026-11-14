"""AI search: "find me something fast paced that uses my brain".

1. Discover lists every event the viewer may see, applying the usual
   filters (and an optional radius around where they are).
2. An interpreter (Claude by default, see claude_interpreter.py) reads the
   request together with the viewer's profile and history (top orgs,
   recent events, interests, learned categories, usual travel distance) and
   scores how well each candidate fits the request, with a short reason.
3. That score becomes the `request_match` signal in the likelihood model
   (ranking.py), alongside friends going, distance, tastes and the rest, and
   Discover ranks again.

If the interpreter fails (no credentials, network, refusal, malformed
output), a keyword match takes its place, so search always answers.

Privacy: the interpreter receives the request, tag and org names, titles of
events the viewer interacted with, and the candidate events. Never names of
other people, emails, positions, or anything the viewer marked
`recommendation_use = NONE`. People who turn off `Person.ai_uses_history`
are searched on their request alone.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from backend.objects import DomainRuleError, GeoPoint, Organization, Person, Tag

from .activity import ActivityService
from .discovery import (
    MAX_LIMIT,
    DiscoveryItem,
    DiscoveryPage,
    DiscoveryQuery,
    DiscoveryService,
    RequestMatch,
)
from .preferences import PreferenceService
from .store import GraphStore

log = logging.getLogger(__name__)

MAX_REQUEST_CHARS = 500
MAX_CANDIDATES = 60
MAX_DESCRIPTION_CHARS = 400
DEFAULT_RESULTS = 10
MIN_LEARNED_STRENGTH = 0.25
STOPWORDS = frozenset(
    ["a", "an", "and", "any", "are", "as", "at", "be", "but", "by", "can", "do", "for", "from", "get", "go", "i", "i'd", "i'm", "im", "in", "into", "is", "it", "like", "me", "my", "of", "on", "or", "some", "something", "somewhere", "that", "the", "this", "to", "uses", "use", "want", "wanna", "with", "would", "you", "your"]
)
WORD = re.compile(r"[a-z0-9']+")
STEM_PREFIX = 4  # "robot" matches "robotics"


# --- what the interpreter sees (no personal identifiers) -----------------------


@dataclass(frozen=True)
class CandidateEvent:
    ref: str  # "e1", "e2", ... (not the database id)
    title: str
    description: str
    location: str
    tags: tuple[str, ...]
    host: str  # org name, or "a student" when a person hosts
    starts: str  # e.g. "Fri 2026-11-20 18:00"
    days_away: float
    distance_km: float | None
    friends_going: int


@dataclass(frozen=True)
class ViewerContext:
    interests: tuple[str, ...] = ()
    learned_categories: tuple[tuple[str, float], ...] = ()  # (tag, strength 0..1)
    org_tags: tuple[str, ...] = ()
    top_orgs: tuple[tuple[str, str, bool], ...] = ()  # (name, kind, is_member)
    recent_events: tuple[tuple[str, tuple[str, ...], str], ...] = ()  # (title, tags, status)
    typical_trip_km: float | None = None


@dataclass(frozen=True)
class SearchRequest:
    sentence: str
    today: str  # e.g. "Fri 2026-11-13"
    viewer: ViewerContext | None
    candidates: tuple[CandidateEvent, ...]


@dataclass(frozen=True)
class Interpretation:
    summary: str  # how the request was understood
    matches: dict[str, RequestMatch] = field(default_factory=dict)  # ref -> match


class InterpreterError(Exception):
    """The interpreter couldn't produce a usable answer; fall back."""


class RequestInterpreter(Protocol):
    name: str

    def interpret(self, request: SearchRequest) -> Interpretation: ...


# --- results -------------------------------------------------------------------


@dataclass(frozen=True)
class AiSearchResult:
    page: DiscoveryPage
    understood_as: str
    used_ai: bool
    note: str | None = None  # why AI wasn't used, if it wasn't


class AiSearchService:
    def __init__(
        self,
        store: GraphStore,
        discovery: DiscoveryService,
        preferences: PreferenceService,
        activity: ActivityService,
        interpreter: RequestInterpreter,
    ) -> None:
        self._store = store
        self._discovery = discovery
        self._preferences = preferences
        self._activity = activity
        self.interpreter = interpreter

    def search(
        self,
        viewer_id: str | None,
        sentence: str,
        now: datetime,
        *,
        origin: GeoPoint | None = None,
        max_distance_km: float | None = None,
        limit: int = DEFAULT_RESULTS,
        offset: int = 0,
    ) -> AiSearchResult:
        text = clean_sentence(sentence)
        base = DiscoveryQuery(
            viewer_id=viewer_id, origin=origin, max_distance_km=max_distance_km,
            include_cancelled=False, limit=MAX_LIMIT,
        )
        pool = self._discovery.search(base, now).items[:MAX_CANDIDATES]
        refs = {f"e{i + 1}": item for i, item in enumerate(pool)}
        request = SearchRequest(
            sentence=text,
            today=now.strftime("%a %Y-%m-%d"),
            viewer=self._viewer_context(viewer_id, now),
            candidates=tuple(self._candidate(ref, item, now) for ref, item in refs.items()),
        )
        matches, summary, used_ai, note = self._interpret(request, refs)
        final = DiscoveryQuery(
            viewer_id=viewer_id, origin=origin, max_distance_km=max_distance_km,
            include_cancelled=False, limit=limit, offset=offset,
        )
        page = self._discovery.search(final, now, request_matches=matches)
        return AiSearchResult(page=page, understood_as=summary, used_ai=used_ai, note=note)

    # --- interpretation --------------------------------------------------------

    def _interpret(
        self, request: SearchRequest, refs: dict[str, DiscoveryItem]
    ) -> tuple[dict[str, RequestMatch], str, bool, str | None]:
        if not refs:
            return {}, request.sentence, False, "no events to search"
        try:
            answer = self.interpreter.interpret(request)
        except InterpreterError as error:
            log.warning("AI search fell back to keywords: %s", error)
            # Keywords are weak evidence: a miss stays neutral (0.5), a full
            # match counts as a strong fit (1.0). Never scored as "the opposite".
            matches = {
                refs[c.ref].event.id: RequestMatch(
                    score=0.5 + 0.5 * keyword_match(request.sentence, c)
                )
                for c in request.candidates
            }
            return matches, request.sentence, False, f"AI unavailable ({error}); matched keywords"
        # Every candidate gets a score; one the interpreter skipped counts as no match.
        matches = {
            item.event.id: answer.matches.get(ref, RequestMatch(score=0.0))
            for ref, item in refs.items()
        }
        return matches, answer.summary or request.sentence, True, None

    # --- building the request --------------------------------------------------

    def _viewer_context(self, viewer_id: str | None, now: datetime) -> ViewerContext | None:
        if viewer_id is None:
            return None
        person = self._store.node(viewer_id, Person)
        if not person.ai_uses_history:
            return None
        profile = self._preferences.profile(viewer_id, now)
        label = self._tag_label
        learned = sorted(
            ((label(t), round(s, 2)) for t, s in profile.learned.items()
             if s >= MIN_LEARNED_STRENGTH),
            key=lambda pair: (-pair[1], pair[0]),
        )
        typical = profile.typical_trip_km
        return ViewerContext(
            interests=tuple(sorted(label(t) for t in profile.chosen)),
            learned_categories=tuple(learned),
            org_tags=tuple(sorted(label(t) for t in profile.org_tags)),
            top_orgs=tuple(
                (o.org.name, o.org.kind.value, o.is_member)
                for o in self._activity.top_orgs(viewer_id, now)
            ),
            recent_events=tuple(
                (e.event.title, tuple(sorted(label(t) for t in e.tag_ids)), e.status.value)
                for e in self._activity.recent_events(viewer_id)
            ),
            typical_trip_km=round(typical, 1) if typical is not None else None,
        )

    def _candidate(self, ref: str, item: DiscoveryItem, now: datetime) -> CandidateEvent:
        event = item.event
        host = item.host.name if isinstance(item.host, Organization) else "a student"
        tags = sorted(self._tag_label(t) for t in self._discovery.event_tag_ids(event.id))
        return CandidateEvent(
            ref=ref,
            title=event.title,
            description=event.description[:MAX_DESCRIPTION_CHARS],
            location=event.location,
            tags=tuple(tags),
            host=host,
            starts=event.starts_at.strftime("%a %Y-%m-%d %H:%M"),
            days_away=round((event.starts_at - now).total_seconds() / 86400, 1),
            distance_km=round(item.distance_km, 1) if item.distance_km is not None else None,
            friends_going=len(item.friends_going),
        )

    def _tag_label(self, tag_id: str) -> str:
        node = self._store.any_node(tag_id) if self._store.has_node(tag_id) else None
        return node.label if isinstance(node, Tag) else tag_id


def clean_sentence(sentence: str) -> str:
    if not isinstance(sentence, str):
        raise DomainRuleError("say what you're looking for")
    text = " ".join(sentence.split())
    if not text:
        raise DomainRuleError("say what you're looking for")
    if len(text) > MAX_REQUEST_CHARS:
        raise DomainRuleError(f"keep it under {MAX_REQUEST_CHARS} characters")
    return text


def keyword_match(sentence: str, candidate: CandidateEvent) -> float:
    """Fallback when AI isn't available: the share of the request's words
    (ignoring filler words) found in the event, 0..1. Words match on a
    shared stem ("robot" ~ "robotics"). No meaningful words -> 0."""
    wanted = {w for w in WORD.findall(sentence.lower()) if w not in STOPWORDS and len(w) > 2}
    if not wanted:
        return 0.0
    text = " ".join(
        (candidate.title, candidate.description, candidate.location, candidate.host,
         " ".join(candidate.tags))
    ).lower()
    present = set(WORD.findall(text))
    found = sum(1 for w in wanted if any(_same_stem(w, p) for p in present))
    return found / len(wanted)


def _same_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= STEM_PREFIX and longer.startswith(shorter)
