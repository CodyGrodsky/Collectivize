"""A small local web server for the demo: the frontend pages plus a JSON API.

    uv run python scripts/serve.py            # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai    # search uses keywords only, no API calls

Access (v1.1 spec 4.10): campus-only. Signing in with a Northeastern email
starts a session (an HttpOnly cookie). Signed out, only the landing/sign-in
page, the invite page, their assets and POST /api/signin answer; every
other page redirects to the landing page and every other API call is 401.
The viewer is always the session's person, never a request parameter.

Platform admins can "view as" someone from /admin: read-only (every change
is refused), with a banner on every page, and logged (see sessions.py).

Safety:
- Demo only: sign-in has no email verification yet, so the server refuses
  to run against the production database.
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
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from backend.app_api import AppApi
from backend.demo_api import (  # re-exported: callers import these from here
    PER_PAGE,
    ApiError,
    DiscoverApi,
    demo_graph_factory,
)
from backend.graph import NotFoundError, PermissionDeniedError
from backend.objects import DomainRuleError
from backend.objects.base import utc_now
from backend.sessions import COOKIE_NAME, Session, SessionStore

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIRS = ("src/frontend", "public", "app")
LANDING_PAGE = "/src/frontend/index.html"
HOME_PAGE = LANDING_PAGE
SIGNED_IN_HOME = "/src/frontend/discover.html"
ADMIN_PAGE = "/src/frontend/admin.html"
# Pages a signed-out visitor may open (v1.1 spec 4.10). Assets (CSS, JS,
# images) are always served: they hold no one's data.
INVITE_PAGE = "/src/frontend/invite.html"
PUBLIC_PAGES = frozenset({LANDING_PAGE, INVITE_PAGE})
MAX_BODY_BYTES = 4096
MAX_UPLOAD_BYTES = 300_000  # profile pictures, resized in the browser first
UPLOAD_ROUTES = frozenset({"/api/profile/photo"})
READ_ONLY_ALLOWED = frozenset({"/api/signout", "/api/admin/view-as"})

__all__ = ["PER_PAGE", "ApiError", "DemoServer", "DiscoverApi", "RequestHandler",
           "demo_graph_factory", "resolve_static", "serve"]


# --- HTTP ----------------------------------------------------------------------------


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        api: DiscoverApi,
        app: AppApi | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        super().__init__(address, RequestHandler)
        self.api = api
        self.app = app or AppApi(api.graphs)
        self.sessions = sessions or SessionStore()


class RequestHandler(BaseHTTPRequestHandler):
    server: DemoServer
    server_version = "CollectivizeDemo"
    sys_version = ""

    def do_GET(self) -> None:
        """API routes, the home redirect, or a static file."""
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            self._get_api(url.path, params)
        elif url.path == "/":
            self._redirect(SIGNED_IN_HOME if self._session() else LANDING_PAGE)
        elif url.path == "/admin":
            self._redirect(ADMIN_PAGE)
        elif url.path.startswith("/invite/"):  # shareable invite links
            code = url.path.removeprefix("/invite/").strip("/")
            self._redirect(INVITE_PAGE + "?" + urlencode({"code": code[:100]}))
        else:
            self._static(url.path)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/signin":
            self._sign_in()
            return
        session = self._session()
        if path == "/api/signout":
            self.server.sessions.end(self._cookie())
            self._json(HTTPStatus.OK, {"signed_out": True}, cookie="")
            return
        if session is None:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "sign in first", "signed_out": True})
            return
        if session.read_only and path not in READ_ONLY_ALLOWED:
            self._json(HTTPStatus.FORBIDDEN, {
                "error": "Read-only: you're viewing as someone. Exit view-as to make changes.",
            })
            return
        routes = self._post_routes(session)
        handler = routes.get(path)
        limit = MAX_UPLOAD_BYTES if path in UPLOAD_ROUTES else MAX_BODY_BYTES
        self._api(None if handler is None else lambda: handler(self._read_json(limit)))

    def _get_api(self, path: str, params: dict[str, str]) -> None:
        app = self.server.app
        if path == "/api/invite":  # the invite landing page is public
            self._api(lambda: app.invite_preview(params))
            return
        session = self._session()
        if path == "/api/session":  # public: lets the landing page check quietly
            self._json(HTTPStatus.OK, {"signed_in": session is not None})
            return
        if session is None:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "sign in first", "signed_out": True})
            return
        if path == "/api/photo":
            self._photo(session.viewer_id, params)
            return
        self._api(self._get_routes(session, params).get(path))

    def _get_routes(
        self, session: Session, params: dict[str, str]
    ) -> dict[str, Callable[[], dict[str, Any]]]:
        api, app, viewer = self.server.api, self.server.app, session.viewer_id
        own = {**params, "viewer": viewer, "near": "0"}  # no on/off-campus switch any more
        return {
            "/api/me": lambda: app.me(session.person_id, session.view_as),
            "/api/tags": lambda: api.tags(),
            "/api/discover": lambda: api.discover(own),
            "/api/calendar": lambda: api.calendar(own),
            "/api/org": lambda: app.org(viewer, params),
            "/api/network": lambda: app.network(viewer, params),
            "/api/profile": lambda: app.profile(viewer, params),
            "/api/find": lambda: app.find(viewer, params, debug=self._is_platform_admin(session)),
            "/api/requests": lambda: app.requests(viewer),
            "/api/my-events": lambda: app.my_events(viewer),
            "/api/my-calendar": lambda: app.my_calendar(viewer, params),
            "/api/admin/users": lambda: app.admin_users(session.person_id, params),
            "/api/admin/audit": lambda: self._audit(session),
        }

    def _post_routes(self, session: Session) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        api, app, viewer = self.server.api, self.server.app, session.viewer_id

        def own(handler: Callable[[dict[str, Any]], dict[str, Any]]):
            return lambda body: handler({**body, "viewer": viewer, "near": False})

        return {
            "/api/search": own(api.search),
            "/api/rsvp": own(api.rsvp),
            "/api/star": own(api.star),
            "/api/org": lambda body: app.update_org(viewer, body),
            "/api/connect": lambda body: app.connect(viewer, body),
            "/api/invites": lambda body: app.create_invite(viewer),
            "/api/profile": lambda body: app.update_profile(viewer, body),
            "/api/profile/photo": lambda body: app.upload_photo(viewer, body),
            "/api/connection": lambda body: app.connection(viewer, body),
            "/api/org/members": lambda body: app.org_members(viewer, body),
            "/api/admin/view-as": lambda body: self._view_as(session, body),
        }

    # --- sessions ----------------------------------------------------------------

    def _cookie(self) -> str | None:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _session(self) -> Session | None:
        session = self.server.sessions.get(self._cookie())
        if session is None:
            return None
        # A deleted account (or a deleted view-as target) ends the session.
        with self.server.api.graphs() as graph:
            if not graph.store.has_node(session.person_id):
                self.server.sessions.end(self._cookie())
                return None
            if session.view_as and not graph.store.has_node(session.view_as):
                return self.server.sessions.view_as(self._cookie() or "", None, utc_now())
        return session

    def _is_platform_admin(self, session: Session | None) -> bool:
        if session is None:
            return False
        with self.server.api.graphs() as graph:
            return graph.visibility.is_platform_admin(session.person_id)

    def _sign_in(self) -> None:
        try:
            person_id = self.server.app.sign_in(self._read_json(MAX_BODY_BYTES))
        except ApiError as error:
            self._json(error.status, {"error": error.message})
            return
        except Exception:
            log.exception("sign-in failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "something went wrong"})
            return
        self.server.sessions.end(self._cookie())
        token = self.server.sessions.create(person_id)
        self._json(HTTPStatus.OK, self.server.app.me(person_id, None), cookie=token)

    def _view_as(self, session: Session, body: dict[str, Any]) -> dict[str, Any]:
        target = self.server.app.check_view_as_target(session.person_id, body.get("person_id"))
        updated = self.server.sessions.view_as(self._cookie() or "", target, utc_now())
        return self.server.app.me(updated.person_id, updated.view_as)

    def _audit(self, session: Session) -> dict[str, Any]:
        self.server.app.require_platform_admin(session.person_id)
        return {"entries": list(reversed(self.server.sessions.audit[-100:]))}

    def _photo(self, viewer: str, params: dict[str, str]) -> None:
        try:
            content_type, data = self.server.app.photo(viewer, params)
        except ApiError as error:
            self._json(error.status, {"error": error.message})
            return
        except Exception:
            log.exception("photo request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "something went wrong"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

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

    def _read_json(self, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad Content-Length") from None
        if not 0 < length <= limit:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, RecursionError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid JSON") from None
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "send a JSON object")
        return body

    def _json(
        self, status: HTTPStatus, payload: dict[str, Any], *, cookie: str | None = None
    ) -> None:
        """`cookie`: a session token to set, or "" to clear the session."""
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        if cookie is not None:
            age = "" if cookie else "; Max-Age=0"
            self.send_header(
                "Set-Cookie", f"{COOKIE_NAME}={cookie}; Path=/; HttpOnly; SameSite=Lax{age}"
            )
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
        if target.suffix == ".html":
            public = urlsplit(url_path).path in PUBLIC_PAGES
            session = None if public else self._session()
            if not public and session is None:
                self._redirect(LANDING_PAGE + "?" + urlencode({"next": url_path}))
                return
            if url_path == ADMIN_PAGE and not self._is_platform_admin(session):
                self._redirect(SIGNED_IN_HOME)  # /admin is for platform admins only
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


def serve(
    api: DiscoverApi,
    port: int = 8000,
    *,
    app: AppApi | None = None,
    sessions: SessionStore | None = None,
) -> DemoServer:
    """Create the server on 127.0.0.1 (call .serve_forever() to run it)."""
    return DemoServer(("127.0.0.1", port), api, app, sessions)
