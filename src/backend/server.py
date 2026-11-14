"""A small local web server for the demo: the frontend pages plus a JSON API.

    uv run python scripts/serve.py            # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai    # search uses keywords only, no API calls

API (JSON):
    GET  /api/personas                         the demo people you can view as
    GET  /api/tags                             topic tags, for filters
    GET  /api/discover?viewer=&page=&near=1&tag=   the ranked Discover list (10 a page)
    POST /api/search  {"viewer", "query", "near"}  AI search (the search agent)

Safety:
- Demo only. There is no login yet, so the server refuses to run against the
  production database: "viewing as" any person is a demo feature, not auth.
- Listens on 127.0.0.1 only.
- Serves static files only from src/frontend, public and app (never data/,
  source code or dotfiles); every path is resolved and checked.
- Every input is validated; errors come back as {"error": "..."} without
  internals. JSON responses carry no-store and nosniff headers.
- AI search: identical searches are answered from a small in-memory cache,
  and only one runs at a time, so a busy page can't run up API costs.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from backend.config import DatabaseConfig, Environment
from backend.graph import (
    CollectivizeGraph,
    DiscoveryQuery,
    NotFoundError,
    PermissionDeniedError,
)
from backend.graph.ai_search import InterpreterError
from backend.graph.discovery import DiscoveryItem
from backend.graph.search_agent import AgentStep
from backend.graph.sqlite_store import SqliteGraphStore
from backend.objects import (
    DomainRuleError,
    MemberOf,
    Organization,
    Person,
    Tag,
)
from backend.objects.base import utc_now
from backend.seed import CAMPUS

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIRS = ("src/frontend", "public", "app")
HOME_PAGE = "/src/frontend/index.html"
PER_PAGE = 10  # collectivize_plan.md: numbered pages of 10, no infinite scroll
MAX_BODY_BYTES = 4096
CACHE_SIZE = 64
FEATURED = ("drew", "avery", "casey", "kai")  # suggested personas, by first name

GraphFactory = Callable[[], AbstractContextManager[CollectivizeGraph]]


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _NoAiInterpreter:
    """Used with --no-ai: always 'unavailable', so search uses keywords."""

    name = "none"

    def interpret(self, request: Any) -> Any:
        raise InterpreterError("AI search is turned off on this server")


class _NoAiClient:
    """Used with --no-ai: the agent can't reach the API and falls back."""

    @property
    def beta(self) -> Any:
        import anthropic

        raise anthropic.AnthropicError("AI search is turned off on this server")


def demo_graph_factory(config: DatabaseConfig, *, use_ai: bool) -> GraphFactory:
    """Each request opens its own connection (SQLite connections can't be
    shared across the server's threads)."""
    if config.environment is not Environment.TEST:
        raise PermissionError(
            "the demo server only runs on the test database: there is no login yet"
        )

    @contextmanager
    def factory() -> Iterator[CollectivizeGraph]:
        store = SqliteGraphStore(config.path, config.environment)
        try:
            if use_ai:
                yield CollectivizeGraph(store)
            else:
                yield CollectivizeGraph(
                    store, search_interpreter=_NoAiInterpreter(), agent_client=_NoAiClient()
                )
        finally:
            store.close()

    return factory


class DiscoverApi:
    """The API's behaviour, independent of HTTP (so it can be tested directly)."""

    def __init__(
        self, graphs: GraphFactory, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._graphs = graphs
        self._clock = clock
        self._search_lock = threading.Lock()
        self._cache: OrderedDict[tuple[str | None, str, bool], dict[str, Any]] = OrderedDict()

    # --- endpoints ---------------------------------------------------------------

    def personas(self) -> dict[str, Any]:
        """Every demo person you can view Discover as, suggested ones first."""
        with self._graphs() as graph:
            people = []
            for person in graph.store.nodes(Person):
                orgs = [
                    (graph.store.node(m.target_id, Organization).name, m.is_admin)
                    for m in graph.store.edges(MemberOf, source_id=person.id)
                    if m.is_confirmed_current
                ]
                first = person.display_name.split(" ")[0]
                people.append({
                    "id": person.id,
                    "name": first,
                    "description": _describe(person.display_name, orgs),
                    "friends": len(graph.connections.connections_of(person.id)),
                    "featured": first.lower() in FEATURED,
                })
        rank = {name: i for i, name in enumerate(FEATURED)}
        people.sort(key=lambda p: (rank.get(p["name"].lower(), len(FEATURED)), p["name"]))
        return {"personas": people}

    def tags(self) -> dict[str, Any]:
        """All topic tags, by label (for the topic filter)."""
        with self._graphs() as graph:
            tags = sorted(graph.store.nodes(Tag), key=lambda t: t.label)
        return {"tags": [{"slug": t.slug, "label": t.label} for t in tags]}

    def discover(self, params: dict[str, str]) -> dict[str, Any]:
        """One page of Discover for a viewer: `viewer`, `page`, `near=1`, `tag`."""
        page = _int_param(params.get("page", "1"), "page", 1, 1000)
        near = params.get("near", "0") == "1"
        with self._graphs() as graph:
            viewer = self._viewer(graph, params.get("viewer"))
            tag_ids = self._tag_ids(graph, params.get("tag"))
            query = DiscoveryQuery(
                viewer_id=viewer, tag_ids=tag_ids, origin=CAMPUS if near else None,
                limit=PER_PAGE, offset=(page - 1) * PER_PAGE,
            )
            result = graph.discovery.search(query, self._clock())
            items = [_item_json(i) for i in result.items]
        return {"items": items, "total": result.total, "page": page, "per_page": PER_PAGE,
                "pages": max(1, -(-result.total // PER_PAGE))}

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        """AI search via the search agent: `query`, `viewer`, `near`. Repeat
        searches come from the cache; one runs at a time."""
        text = body.get("query")
        if not isinstance(text, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "say what you're looking for")
        near = body.get("near") is True
        viewer_param = body.get("viewer")
        if viewer_param is not None and not isinstance(viewer_param, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown person")
        key = (viewer_param or None, " ".join(text.split()).casefold(), near)
        with self._search_lock:  # one AI search at a time
            if key in self._cache:
                self._cache.move_to_end(key)
                return {**self._cache[key], "cached": True}
            with self._graphs() as graph:
                viewer = self._viewer(graph, viewer_param)
                result = graph.search_agent.search(
                    viewer, text, self._clock(), origin=CAMPUS if near else None
                )
                answer = {
                    "goal": result.goal,
                    "used_ai": result.used_agent,
                    "note": result.note,
                    "steps": [_step_json(s) for s in result.steps],
                    "items": [_item_json(i) for i in result.page.items],
                }
            self._cache[key] = answer
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return {**answer, "cached": False}

    # --- helpers -------------------------------------------------------------------

    @staticmethod
    def _viewer(graph: CollectivizeGraph, viewer_id: str | None) -> str | None:
        if not viewer_id:
            return None
        if not graph.store.has_node(viewer_id) or not isinstance(
            graph.store.any_node(viewer_id), Person
        ):
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown person")
        return viewer_id

    @staticmethod
    def _tag_ids(graph: CollectivizeGraph, slug: str | None) -> tuple[str, ...]:
        if not slug:
            return ()
        found = [t.id for t in graph.store.nodes(Tag) if t.slug == slug]
        if not found:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown topic")
        return tuple(found)


# --- JSON shapes -------------------------------------------------------------------


def _item_json(item: DiscoveryItem) -> dict[str, Any]:
    event = item.event
    host_is_org = isinstance(item.host, Organization)
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat(),
        "location": event.location,
        "host": {
            "name": item.host.name if host_is_org else item.host.display_name,
            "kind": "org" if host_is_org else "person",
        },
        "score": round(item.score, 3),
        "reasons": list(item.reasons),
        "friends_going": [p.display_name for p in item.friends_going],
        "going_count": item.going_count,
        "my_rsvp": item.my_rsvp.value if item.my_rsvp else None,
        "distance_km": None if item.distance_km is None else round(item.distance_km, 2),
        "is_cancelled": item.is_cancelled,
    }


STEP_TEXT = {
    "get_person_overview": "Looked at your interests and the kinds of events you go to",
    "get_active_orgs": "Checked the orgs you're most active in",
    "get_recent_events": "Looked at events you've been to lately",
    "list_tags": "Checked which topics events can have",
    "submit_picks": "Picked the best matches",
}


def _step_json(step: AgentStep) -> dict[str, Any]:
    text = STEP_TEXT.get(step.tool, step.tool.replace("_", " "))
    if step.tool == "search_events":
        parts = []
        if step.input.get("keywords"):
            parts.append(f"“{step.input['keywords']}”")
        if step.input.get("tags"):
            parts.append("topics: " + ", ".join(step.input["tags"]))
        if step.input.get("org_name"):
            parts.append(f"from {step.input['org_name']}")
        if step.input.get("within_days"):
            parts.append(f"in the next {step.input['within_days']} days")
        if step.input.get("max_distance_km"):
            parts.append(f"within {step.input['max_distance_km']} km")
        text = "Searched events" + (": " + ", ".join(parts) if parts else "")
    return {"tool": step.tool, "text": text, "error": step.is_error}


def _describe(display_name: str, orgs: list[tuple[str, bool]]) -> str:
    role = display_name.split("(", 1)[1].rstrip(")") if "(" in display_name else ""
    names = [name.removeprefix("[Demo] ") for name, _ in orgs]
    where = ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
    return " · ".join(part for part in (role.removeprefix("demo "), where) if part)


def _int_param(value: str, name: str, low: int, high: int) -> int:
    if not value.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be a whole number")
    number = int(value)
    if not low <= number <= high:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be between {low} and {high}")
    return number


# --- HTTP ----------------------------------------------------------------------------


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: DiscoverApi) -> None:
        super().__init__(address, RequestHandler)
        self.api = api


class RequestHandler(BaseHTTPRequestHandler):
    server: DemoServer
    server_version = "CollectivizeDemo"
    sys_version = ""

    def do_GET(self) -> None:
        """API routes, the home redirect, or a static file."""
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            routes = {
                "/api/personas": lambda: self.server.api.personas(),
                "/api/tags": lambda: self.server.api.tags(),
                "/api/discover": lambda: self.server.api.discover(params),
            }
            self._api(routes.get(url.path))
        elif url.path == "/":
            self._redirect(HOME_PAGE)
        else:
            self._static(url.path)

    def do_POST(self) -> None:
        """POST /api/search only."""
        if urlsplit(self.path).path != "/api/search":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._api(lambda: self.server.api.search(self._read_json()))

    def log_message(self, format: str, *args: Any) -> None:
        """One short log line per request (method and path, no query)."""
        log.info("%s %s", self.command, urlsplit(self.path).path)

    # --- internals -----------------------------------------------------------

    def _api(self, handler: Callable[[], dict[str, Any]] | None) -> None:
        if handler is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            self._json(HTTPStatus.OK, handler())
        except ApiError as error:
            self._json(error.status, {"error": error.message})
        except (DomainRuleError, NotFoundError, PermissionDeniedError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception:  # never leak internals to the page
            log.exception("API request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "something went wrong"})

    def _read_json(self) -> dict[str, Any]:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad Content-Length") from None
        if not 0 < length <= MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, RecursionError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid JSON") from None
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "send a JSON object")
        return body

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _static(self, url_path: str) -> None:
        target = resolve_static(url_path)
        if target is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")


def resolve_static(url_path: str, root: Path = ROOT) -> Path | None:
    """The file a URL path refers to, or None unless it is a real file inside
    one of STATIC_DIRS. Blocks '..', encoded traversal, dotfiles and symlinks
    that lead outside."""
    relative = unquote(url_path).lstrip("/")
    if not relative or "\x00" in relative:
        return None
    if any(part.startswith(".") for part in Path(relative).parts):
        return None
    candidate = (root / relative).resolve()
    allowed = [(root / d).resolve() for d in STATIC_DIRS]
    if not any(candidate.is_relative_to(base) for base in allowed):
        return None
    return candidate if candidate.is_file() else None


def serve(api: DiscoverApi, port: int = 8000) -> DemoServer:
    """Create the server on 127.0.0.1 (call .serve_forever() to run it)."""
    return DemoServer(("127.0.0.1", port), api)
