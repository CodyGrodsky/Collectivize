"""A small local web server for the demo: the frontend pages plus a JSON API.

    uv run python scripts/serve.py            # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai    # search uses keywords only, no API calls

API (JSON):
    GET  /api/personas                         the demo people you can view as
    GET  /api/tags                             topic tags, for filters
    GET  /api/discover?viewer=&page=&near=1&tag=   the ranked Discover list (10 a page)
    POST /api/search  {"viewer", "query", "near"}  AI search (the search agent)
    GET  /api/network?viewer=&center=          the network map (you or a connection)

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
import sqlite3
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from backend.demo_api import (  # re-exported: callers import these from here
    PER_PAGE,
    ApiError,
    DiscoverApi,
    demo_graph_factory,
)
from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import DomainRuleError

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIRS = ("src/frontend", "public", "app")
HOME_PAGE = "/src/frontend/index.html"
MAX_BODY_BYTES = 4096

__all__ = ["PER_PAGE", "ApiError", "DemoServer", "DiscoverApi", "RequestHandler",
           "demo_graph_factory", "resolve_static", "serve"]


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
                "/api/calendar": lambda: self.server.api.calendar(params),
                "/api/network": lambda: self.server.api.network(params),
            }
            self._api(routes.get(url.path))
        elif url.path == "/":
            self._redirect(HOME_PAGE)
        else:
            self._static(url.path)

    def do_POST(self) -> None:
        """POST /api/search, /api/rsvp and /api/star."""
        routes = {
            "/api/search": self.server.api.search,
            "/api/rsvp": self.server.api.rsvp,
            "/api/star": self.server.api.star,
        }
        handler = routes.get(urlsplit(self.path).path)
        self._api(None if handler is None else lambda: handler(self._read_json()))

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
        except NotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "that isn't available (it may have been removed)"})
        except PermissionDeniedError as error:
            self._json(HTTPStatus.FORBIDDEN, {"error": str(error)})
        except DomainRuleError as error:  # a rule said no: full, ended, bad input...
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except sqlite3.OperationalError:  # e.g. "database is locked" by another save
            log.warning("database busy", exc_info=True)
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "busy right now, please try again"})
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
