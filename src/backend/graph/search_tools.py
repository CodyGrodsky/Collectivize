"""The tools the search agent may call, bound to one viewer and one search.

Every tool:
- sees only what this viewer may see (searches go through Discover with
  the viewer's id, so visibility and filters apply),
- returns compact JSON with no names of people, emails, positions or
  database ids (events are "e1", "e2", ... for this search only),
- treats its input as untrusted: bad input returns an error message for the
  agent to correct, never an exception.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from backend.objects import GeoPoint, Organization, Person, Tag

from .activity import ActivityService
from .ai_search import CandidateEvent, keyword_match
from .discovery import MAX_LIMIT, DiscoveryItem, DiscoveryQuery, DiscoveryService
from .preferences import PreferenceService
from .privacy import PrivacyService
from .store import GraphStore

MAX_RESULTS_PER_SEARCH = 15
MAX_PICKS = 5
MAX_DESCRIPTION_CHARS = 300
MAX_TEXT_INPUT = 200
MAX_WITHIN_DAYS = 365
MAX_RADIUS_KM = 500.0


class ToolInputError(ValueError):
    """The agent sent unusable input; the message goes back to it."""


@dataclass(frozen=True)
class Pick:
    ref: str
    fit: float
    reason: str


@dataclass
class SearchSession:
    """State for one agent run: which events the agent has seen, and its picks."""

    viewer_id: str | None
    now: datetime
    origin: GeoPoint | None
    seen: dict[str, DiscoveryItem] = field(default_factory=dict)  # ref -> item
    refs_by_event: dict[str, str] = field(default_factory=dict)  # event id -> ref
    goal: str | None = None
    picks: list[Pick] | None = None

    def ref_for(self, item: DiscoveryItem) -> str:
        if item.event.id not in self.refs_by_event:
            ref = f"e{len(self.refs_by_event) + 1}"
            self.refs_by_event[item.event.id] = ref
        ref = self.refs_by_event[item.event.id]
        self.seen[ref] = item
        return ref


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_person_overview",
        "description": "What the searcher likes: interests they chose, categories of events "
                       "they tend to go to (0-1 strength), their orgs' topics, how far they "
                       "usually travel, and whether their current location is known.",
        "input_schema": _obj({}),
    },
    {
        "name": "get_active_orgs",
        "description": "The organizations the searcher interacts with most, strongest first.",
        "input_schema": _obj({"limit": {"type": "integer"}}),
    },
    {
        "name": "get_recent_events",
        "description": "Events the searcher recently said they're going to or interested in, "
                       "newest first.",
        "input_schema": _obj({"limit": {"type": "integer"}}),
    },
    {
        "name": "list_tags",
        "description": "All topic tags events can have (slug and label).",
        "input_schema": _obj({}),
    },
    {
        "name": "search_events",
        "description": "Search upcoming events the searcher may see. Every filter is optional "
                       "(use null or []). keywords: words to look for in title, description, "
                       "location, host and tags (events matching any word, best first). "
                       "tags: tag slugs; events must have at least one. org_name: an org, "
                       "including the orgs under it. within_days: starts within this many "
                       "days. max_distance_km: only events this close (needs a known "
                       "location). Results include how likely the app already thinks the "
                       "searcher is to go, from friends, distance, tastes and timing.",
        "input_schema": _obj({
            "keywords": {"type": ["string", "null"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "org_name": {"type": ["string", "null"]},
            "within_days": {"type": ["integer", "null"]},
            "max_distance_km": {"type": ["number", "null"]},
            "limit": {"type": "integer"},
        }),
    },
    {
        "name": "submit_picks",
        "description": f"Finish: state the searcher's goal in one sentence and give up to "
                       f"{MAX_PICKS} best events from your searches, best first. fit is 0-1 "
                       f"(1 = exactly what they want). reason: under 15 words, to the searcher.",
        "input_schema": _obj({
            "goal": {"type": "string"},
            "picks": {"type": "array", "items": _obj({
                "ref": {"type": "string"},
                "fit": {"type": "number"},
                "reason": {"type": "string"},
            })},
        }),
    },
]
FINISH_TOOL = "submit_picks"


class SearchTools:
    def __init__(
        self,
        store: GraphStore,
        discovery: DiscoveryService,
        preferences: PreferenceService,
        activity: ActivityService,
        privacy: PrivacyService,
        session: SearchSession,
    ) -> None:
        self._store = store
        self._privacy = privacy
        self._discovery = discovery
        self._preferences = preferences
        self._activity = activity
        self.session = session
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "get_person_overview": self._overview,
            "get_active_orgs": self._active_orgs,
            "get_recent_events": self._recent_events,
            "list_tags": self._list_tags,
            "search_events": self._search_events,
            FINISH_TOOL: self._submit,
        }

    def run(self, name: str, tool_input: Any) -> tuple[str, bool]:
        """Run one tool. Returns (JSON text, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name!r}"}), True
        if not isinstance(tool_input, dict):
            return json.dumps({"error": "input must be an object"}), True
        try:
            return json.dumps(handler(tool_input), ensure_ascii=False), False
        except ToolInputError as error:
            return json.dumps({"error": str(error)}), True

    # --- about the person -------------------------------------------------------

    def _overview(self, _: dict[str, Any]) -> dict[str, Any]:
        if self.session.viewer_id is None or not self._uses_history():
            return {"note": "no profile available; work from the request alone",
                    "location_known": self.session.origin is not None}
        profile = self._preferences.profile(self.session.viewer_id, self.session.now)
        label = self._label
        return {
            "interests": sorted(label(t) for t in profile.chosen),
            "goes_to_categories": {
                label(t): round(s, 2)
                for t, s in sorted(profile.learned.items(), key=lambda kv: -kv[1])
                if s >= 0.25
            },
            "org_topics": sorted(label(t) for t in profile.org_tags),
            "usually_travels_km": (
                None if profile.typical_trip_km is None else round(profile.typical_trip_km, 1)
            ),
            "location_known": self.session.origin is not None,
        }

    def _active_orgs(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        if self.session.viewer_id is None or not self._uses_history():
            return []
        limit = _int(data.get("limit"), "limit", 1, 10)
        return [
            {"name": o.org.name, "kind": o.org.kind.value, "member": o.is_member,
             "activity": round(o.score, 2)}
            for o in self._activity.top_orgs(self.session.viewer_id, self.session.now, limit)
        ]

    def _recent_events(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        if self.session.viewer_id is None or not self._uses_history():
            return []
        limit = _int(data.get("limit"), "limit", 1, 15)
        return [
            {"title": e.event.title, "tags": sorted(self._label(t) for t in e.tag_ids),
             "status": e.status.value,
             "days_ago": round((self.session.now - e.at).total_seconds() / 86400, 1)}
            for e in self._activity.recent_events(self.session.viewer_id, limit)
        ]

    def _list_tags(self, _: dict[str, Any]) -> list[dict[str, str]]:
        return [{"slug": t.slug, "label": t.label}
                for t in sorted(self._store.nodes(Tag), key=lambda t: t.slug)]

    # --- searching ---------------------------------------------------------------

    def _search_events(self, data: dict[str, Any]) -> dict[str, Any]:
        s = self.session
        limit = _int(data.get("limit"), "limit", 1, MAX_RESULTS_PER_SEARCH)
        keywords = _text(data.get("keywords"), "keywords")
        radius = data.get("max_distance_km")
        if radius is not None:
            if s.origin is None:
                raise ToolInputError("the searcher's location isn't known; drop max_distance_km")
            radius = _number(radius, "max_distance_km", 0.01, MAX_RADIUS_KM)
        days = data.get("within_days")
        query = DiscoveryQuery(
            viewer_id=s.viewer_id,
            tag_ids=self._tag_ids(data.get("tags")),
            org_id=self._org_id(_text(data.get("org_name"), "org_name")),
            starts_before=(
                None if days is None
                else s.now + timedelta(days=_int(days, "within_days", 1, MAX_WITHIN_DAYS))
            ),
            origin=s.origin,
            max_distance_km=radius,
            include_cancelled=False,
            limit=MAX_LIMIT,
        )
        items = list(self._discovery.search(query, s.now).items)
        if keywords:
            scored = [(keyword_match(keywords, self._candidate(i)), i) for i in items]
            items = [i for score, i in sorted(scored, key=lambda p: -p[0]) if score > 0]
        return {
            "count": len(items),
            "events": [self._event_json(i) for i in items[:limit]],
        }

    def _event_json(self, item: DiscoveryItem) -> dict[str, Any]:
        event = item.event
        return {
            "ref": self.session.ref_for(item),
            "title": event.title,
            "description": event.description[:MAX_DESCRIPTION_CHARS],
            "location": event.location,
            "tags": sorted(self._label(t) for t in self._discovery.event_tag_ids(event.id)),
            "host": item.host.name if isinstance(item.host, Organization) else "a student",
            "starts": event.starts_at.strftime("%a %Y-%m-%d %H:%M"),
            "days_away": round((event.starts_at - self.session.now).total_seconds() / 86400, 1),
            "distance_km": None if item.distance_km is None else round(item.distance_km, 1),
            "friends_going": len(item.friends_going),
            "app_likelihood": round(item.score, 2),
        }

    def _candidate(self, item: DiscoveryItem) -> CandidateEvent:
        event = item.event
        return CandidateEvent(
            ref="", title=event.title, description=event.description,
            location=event.location,
            tags=tuple(self._label(t) for t in self._discovery.event_tag_ids(event.id)),
            host=item.host.name if isinstance(item.host, Organization) else "",
            starts="", days_away=0.0, distance_km=None, friends_going=0,
        )

    # --- finishing ---------------------------------------------------------------

    def _submit(self, data: dict[str, Any]) -> dict[str, Any]:
        goal = _text(data.get("goal"), "goal")
        if not goal:
            raise ToolInputError("goal is required")
        raw = data.get("picks")
        if not isinstance(raw, list) or len(raw) > MAX_PICKS:
            raise ToolInputError(f"picks must be a list of at most {MAX_PICKS}")
        picks: list[Pick] = []
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("ref") not in self.session.seen:
                raise ToolInputError(
                    f"unknown ref {entry.get('ref') if isinstance(entry, dict) else entry!r}; "
                    "pick only events returned by search_events"
                )
            if any(p.ref == entry["ref"] for p in picks):
                raise ToolInputError(f"{entry['ref']} is picked twice")
            fit = _number(entry.get("fit"), "fit", 0.0, 1.0)
            reason = _text(entry.get("reason"), "reason") or ""
            picks.append(Pick(ref=entry["ref"], fit=fit, reason=reason[:120]))
        self.session.goal = goal[:200]
        self.session.picks = picks
        return {"status": "submitted", "picks": len(picks)}

    # --- helpers -----------------------------------------------------------------

    def _uses_history(self) -> bool:
        person = self._store.node(self.session.viewer_id, Person)  # type: ignore[arg-type]
        return person.ai_uses_history

    def _label(self, tag_id: str) -> str:
        node = self._store.any_node(tag_id) if self._store.has_node(tag_id) else None
        return node.label if isinstance(node, Tag) else tag_id

    def _tag_ids(self, slugs: Any) -> tuple[str, ...]:
        if slugs is None:
            return ()
        if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
            raise ToolInputError("tags must be a list of tag slugs")
        by_slug = {t.slug: t.id for t in self._store.nodes(Tag)}
        unknown = [s for s in slugs if s not in by_slug]
        if unknown:
            raise ToolInputError(f"unknown tags {unknown}; call list_tags for valid slugs")
        return tuple(by_slug[s] for s in slugs)

    def _org_id(self, name: str | None) -> str | None:
        if not name:
            return None
        wanted = name.casefold()
        found = [o for o in self._store.nodes(Organization)
                 if wanted in o.name.casefold()
                 and o.is_active and self._privacy.can_view(self.session.viewer_id, o)]
        if not found:
            raise ToolInputError(f"no organization matching {name!r}")
        return min(found, key=lambda o: (len(o.name), o.id)).id


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise ToolInputError(f"{name} must be between {low} and {high}")
    return value


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolInputError(f"{name} must be a number")
    if not low <= value <= high:
        raise ToolInputError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def _text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{name} must be text")
    if len(value) > MAX_TEXT_INPUT:
        raise ToolInputError(f"{name} is limited to {MAX_TEXT_INPUT} characters")
    return " ".join(value.split())
