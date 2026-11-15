"""A small local web server for the demo: the frontend pages plus a JSON API.

    uv run python scripts/serve.py            # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai    # search uses keywords only, no API calls

API (JSON; the viewer is always the signed-in person):
    POST /api/signin {"email"}, /api/signout; GET /api/session, /api/me
    GET  /api/find?q=&types=&tags=&date=&page=   unified search (people, orgs, events)
    GET  /api/network?center=                    a profile and its network map
    POST /api/profile {...changes}               save your own profile and settings
    GET  /api/discover, /api/calendar, /api/my-calendar, /api/my-events, /api/tags
    POST /api/rsvp, /api/star, /api/connect, /api/connection, /api/invites
    GET  /api/requests, /api/org, /api/invite (public); POST /api/org, /api/org/members
    GET  /api/admin/users, /api/admin/audit; POST /api/admin/view-as (platform admins)
    GET  /api/account; POST /api/account, /api/account/signout-all, /api/account/delete
    GET  /api/admin/account?id=; POST /api/admin/account, /api/admin/account/create,
         /api/admin/account/signout, /api/admin/account/delete   (see accounts.py)

Access (v1.1 spec 4.10): campus-only. Signing in with a Northeastern email
starts a session (an HttpOnly cookie). Signed out, only the landing/sign-in
page, the invite page, their assets and POST /api/signin answer; every
other page redirects to the landing page and every other API call is 401.
The viewer is always the session's person, never a request parameter.

Development: the gate can be turned off. Anyone without a session is then
signed in automatically (as drew.demo@northeastern.edu, or `--as <email>`),
with a real session, so everything else behaves exactly as with login on;
sign out and use the sign-in form to switch accounts. Whether `serve.py`
asks for sign-in by default is `LOGIN_REQUIRED` in config.py (temporarily
False); `serve.py --login` / `--no-login` override it for one run. This
module itself always has the gate on unless `auto_login_email` is passed.

Admin sign-in: /admin (not linked anywhere) asks for an admin username and
password (admin_auth.py; POST /api/admin/signin). When the server is given
an AdminAuth, platform-admin accounts can sign in only that way: the email
form and the development auto-login both refuse them.

What each admin may do is set by the owner (admin_policy.py). An admin
sign-in lasts eight hours.

Sent home: when an account is deleted, or signed out everywhere, each of
its browsers gets 401 (pages: a redirect to the landing page) on its next
request, its cookie is cleared, and it is not signed in again
automatically. Pages also check every few seconds (shared.js).

Platform admins can "view as" someone from /admin: read-only (every change
is refused), with a banner on every page, and logged (see sessions.py).

Safety:
- Demo only: sign-in has no email verification yet, so the server refuses
  to run against the production database.
- Listens on 127.0.0.1 only.
- Serves static files only from src/frontend, public and app (never data/,
  source code or dotfiles); every path is resolved and checked.
- A POST whose Origin is another site is refused (on top of SameSite
  cookies and JSON-only bodies), so no other page can act as you.
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

from backend.accounts import AccountsApi
from backend.admin_auth import MAX_PASSWORD, MAX_USERNAME, AdminAuth, LockedOutError
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
ADMIN_LOGIN_PAGE = "/src/frontend/admin-login.html"
# Pages a signed-out visitor may open (v1.1 spec 4.10). Assets (CSS, JS,
# images) are always served: they hold no one's data.
INVITE_PAGE = "/src/frontend/invite.html"
PUBLIC_PAGES = frozenset({LANDING_PAGE, INVITE_PAGE, ADMIN_LOGIN_PAGE})
MAX_BODY_BYTES = 4096
# A full profile edit is bigger: 1000 characters of emoji is about 4 KB alone.
BODY_LIMITS = {"/api/profile": 8192, "/api/admin/account": 8192}
ADMIN_SESSION_SECONDS = 8 * 60 * 60  # an admin sign-in ends by itself
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
        auto_login_email: str | None = None,
        accounts: AccountsApi | None = None,
        admin_auth: AdminAuth | None = None,
    ) -> None:
        self.api = api
        # With an AdminAuth, platform admins sign in only at /admin with
        # their username and password. None (tests, old callers): they
        # sign in by email like anyone else.
        self.admin_auth = admin_auth
        self.app = app or AppApi(api.graphs)
        self.sessions = sessions or SessionStore()
        self.accounts = accounts or AccountsApi(
            api.graphs, self.sessions,
            owners=admin_auth.owner_emails if admin_auth else None,
            logins=admin_auth.emails if admin_auth else frozenset(),
        )
        # Development only (`serve.py --no-login`): visitors without a session
        # are signed in as this account. None (the default) means login is on.
        # Checked before the port opens, so a bad account fails cleanly.
        self.auto_login_id = (
            None if auto_login_email is None
            else self.app.sign_in({"email": auto_login_email})
        )
        if self.auto_login_id and admin_auth and self.is_platform_admin(self.auto_login_id):
            raise ApiError(HTTPStatus.FORBIDDEN,
                           "that is a platform admin: admins sign in at /admin, not automatically")
        super().__init__(address, RequestHandler)

    def is_platform_admin(self, person_id: str) -> bool:
        with self.api.graphs() as graph:
            return graph.visibility.is_platform_admin(person_id)


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
        elif url.path == "/admin":  # the admin entrance: not linked from any page
            is_admin = self._is_platform_admin(self._session())
            self._redirect(ADMIN_PAGE if is_admin else ADMIN_LOGIN_PAGE)
        elif url.path.startswith("/invite/"):  # shareable invite links
            code = url.path.removeprefix("/invite/").strip("/")
            self._redirect(INVITE_PAGE + "?" + urlencode({"code": code[:100]}))
        else:
            self._static(url.path)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "cross-site request refused"})
            return
        if path == "/api/signin":
            self._sign_in()
            return
        if path == "/api/admin/signin":
            self._admin_sign_in()
            return
        session = self._session(auto=path != "/api/signout")
        if path == "/api/signout":
            self.server.sessions.end(self._cookie())
            self._json(HTTPStatus.OK, {"signed_out": True}, cookie="")
            return
        if session is None:
            self._signed_out()
            return
        if session.read_only and path not in READ_ONLY_ALLOWED:
            self._json(HTTPStatus.FORBIDDEN, {
                "error": "Read-only: you're viewing as someone. Exit view-as to make changes.",
            })
            return
        routes = self._post_routes(session)
        handler = routes.get(path)
        limit = BODY_LIMITS.get(path, MAX_BODY_BYTES)
        self._api(None if handler is None else lambda: handler(self._read_json(limit)))

    def _get_api(self, path: str, params: dict[str, str]) -> None:
        app = self.server.app
        if path == "/api/invite":  # the invite landing page is public
            self._api(lambda: app.invite_preview(params))
            return
        # The landing page's quiet check never auto-signs-in, so with
        # --no-login the sign-in form still works for switching accounts.
        session = self._session(auto=path != "/api/session")
        if path == "/api/session":  # public: lets pages check quietly
            reason = None if session else self._ended_reason()
            self._json(HTTPStatus.OK, {"signed_in": session is not None,
                                       **({"reason": reason} if reason else {})})
            return
        if session is None:
            self._signed_out()
            return
        self._api(self._get_routes(session, params).get(path))

    def _get_routes(
        self, session: Session, params: dict[str, str]
    ) -> dict[str, Callable[[], dict[str, Any]]]:
        api, app, viewer = self.server.api, self.server.app, session.viewer_id
        accounts = self.server.accounts
        own = {**params, "viewer": viewer, "near": "0"}  # no on/off-campus switch any more
        return {
            "/api/me": lambda: self._me(session),
            "/api/tags": lambda: api.tags(),
            "/api/discover": lambda: api.discover(own),
            "/api/calendar": lambda: api.calendar(own),
            "/api/org": lambda: app.org(viewer, params),
            "/api/network": lambda: api.network({**params, "viewer": viewer}),
            "/api/find": lambda: app.find(viewer, params, debug=self._is_platform_admin(session)),
            "/api/requests": lambda: app.requests(viewer),
            "/api/my-events": lambda: app.my_events(viewer),
            "/api/my-calendar": lambda: app.my_calendar(viewer, params),
            "/api/admin/users": lambda: accounts.admin_users(session.person_id, params),
            "/api/admin/admins": lambda: accounts.admins(session.person_id),
            "/api/admin/audit": lambda: self._audit(session),
            "/api/account": lambda: accounts.my_account(session.person_id),
            "/api/admin/account": lambda: accounts.admin_account(session.person_id, params),
        }

    def _post_routes(self, session: Session) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        api, app, viewer = self.server.api, self.server.app, session.viewer_id
        accounts, me = self.server.accounts, session.person_id

        def own(handler: Callable[[dict[str, Any]], dict[str, Any]]):
            return lambda body: handler({**body, "viewer": viewer, "near": False})

        return {
            "/api/search": own(api.search),
            "/api/rsvp": own(api.rsvp),
            "/api/star": own(api.star),
            "/api/org": lambda body: app.update_org(viewer, body),
            "/api/connect": lambda body: app.connect(viewer, body),
            "/api/invites": lambda body: app.create_invite(viewer),
            "/api/profile": lambda body: api.profile({**body, "viewer": viewer}),
            "/api/connection": lambda body: app.connection(viewer, body),
            "/api/org/members": lambda body: app.org_members(viewer, body),
            "/api/admin/view-as": lambda body: self._view_as(session, body),
            # Accounts: always the session's own person, never the view-as target.
            "/api/account": lambda body: accounts.update_my_settings(me, body),
            "/api/account/signout-all": lambda body: accounts.sign_out_everywhere(me),
            "/api/account/delete": lambda body: accounts.delete_my_account(me, body),
            "/api/admin/account": lambda body: accounts.admin_update(me, body),
            "/api/admin/account/create": lambda body: accounts.admin_create(me, body),
            "/api/admin/account/signout": lambda body: accounts.admin_sign_out(me, body),
            "/api/admin/account/delete": lambda body: accounts.admin_delete(me, body),
            "/api/admin/admins": lambda body: accounts.set_admin(me, body),
        }

    # --- sessions ----------------------------------------------------------------

    def _same_origin(self) -> bool:
        """False when a browser says this POST comes from another site.
        (Browsers always send Origin on cross-site POSTs; tools without one
        still need the session cookie.)"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == (self.headers.get("Host") or "")

    def _cookie(self) -> str | None:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _session(self, *, auto: bool = True) -> Session | None:
        session = self.server.sessions.get(self._cookie())
        if session is None:
            if self._ended_reason():
                # Deleted or signed out by someone else: back to the home
                # page, and never straight into another account.
                self._clear_cookie = True
                return None
            return self._auto_login() if auto else None
        # A deleted account (or a deleted view-as target) ends the session.
        with self.server.api.graphs() as graph:
            if not graph.store.has_node(session.person_id):
                self.server.sessions.end_all(session.person_id, "deleted")
                self._clear_cookie = True
                return None
            if session.view_as and not graph.store.has_node(session.view_as):
                return self.server.sessions.view_as(self._cookie() or "", None, utc_now())
        return session

    def _auto_login(self) -> Session | None:
        """With --no-login: start a real session for the default account,
        and send its cookie with this response (see end_headers)."""
        person_id = self.server.auto_login_id
        if person_id is None:
            return None
        token = self.server.sessions.create(person_id)
        self._pending_cookie = token
        return self.server.sessions.get(token)

    def _ended_reason(self) -> str | None:
        return self.server.sessions.ended_reason(self._cookie())

    def _signed_out(self) -> None:
        self._json(HTTPStatus.UNAUTHORIZED, {
            "error": "sign in first", "signed_out": True, "reason": self._ended_reason(),
        })

    def _me(self, session: Session) -> dict[str, Any]:
        return {**self.server.app.me(session.person_id, session.view_as),
                **self.server.accounts.rights(session.person_id)}

    def end_headers(self) -> None:
        token = getattr(self, "_pending_cookie", None)
        if token:
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax")
            self._pending_cookie = None
        elif getattr(self, "_clear_cookie", False):
            self.send_header("Set-Cookie", f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self._clear_cookie = False
        super().end_headers()

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
        if self.server.admin_auth and self.server.is_platform_admin(person_id):
            # Admin accounts open only with the admin username and password.
            self._json(HTTPStatus.FORBIDDEN, {"error": "that account can't sign in here"})
            return
        self._start_session(person_id)

    def _start_session(self, person_id: str, ttl: float | None = None) -> None:
        self.server.sessions.end(self._cookie())
        token = self.server.sessions.create(person_id, ttl)
        session = self.server.sessions.get(token)
        self._json(HTTPStatus.OK, self._me(session), cookie=token)

    def _admin_sign_in(self) -> None:
        """POST /api/admin/signin {"username", "password"}. Every failure
        gets the same answer, whatever was wrong."""
        auth, refused = self.server.admin_auth, {"error": "wrong username or password"}
        if auth is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            body = self._read_json(MAX_BODY_BYTES)
            username, password = body.get("username"), body.get("password")
            if not (isinstance(username, str) and isinstance(password, str)
                    and 0 < len(username) <= MAX_USERNAME and 0 < len(password) <= MAX_PASSWORD):
                self._json(HTTPStatus.UNAUTHORIZED, refused)
                return
            email = auth.verify(username, password)
            person_id = self.server.app.admin_account_id(email) if email else None
        except ApiError as error:
            self._json(error.status, {"error": error.message})
            return
        except LockedOutError:
            log.warning("admin sign-in: locked out after too many wrong passwords")
            self._json(HTTPStatus.TOO_MANY_REQUESTS,
                       {"error": "too many wrong passwords: try again in 15 minutes"})
            return
        except Exception:
            log.exception("admin sign-in failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "something went wrong"})
            return
        now = utc_now()
        if person_id is None:
            self.server.accounts.audit.record("", "sign-in failed", "", now)
            self._json(HTTPStatus.UNAUTHORIZED, refused)
            return
        self.server.accounts.audit.record(person_id, "sign-in", person_id, now)
        self._start_session(person_id, ADMIN_SESSION_SECONDS)

    def _view_as(self, session: Session, body: dict[str, Any]) -> dict[str, Any]:
        target = self.server.accounts.check_view_as(session.person_id, body.get("person_id"))
        updated = self.server.sessions.view_as(self._cookie() or "", target, utc_now())
        return self._me(updated)

    def _audit(self, session: Session) -> dict[str, Any]:
        self.server.app.require_platform_admin(session.person_id)
        views = [{**e, "action": f"view-as {e['action']}", "detail": ""}
                 for e in self.server.sessions.audit]
        entries = sorted(views + self.server.accounts.audit.entries(), key=lambda e: e["at"])
        return {"entries": list(reversed(entries[-100:]))}

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
        except (BrokenPipeError, ConnectionResetError):
            # The browser stopped waiting (left the page, or asked again) before
            # the answer was sent. Nothing failed and there is nobody to tell.
            log.info("client left before the answer was sent: %s", urlsplit(self.path).path)
            self.close_connection = True
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
                reason = self._ended_reason()  # e.g. "deleted": say so, don't bounce back
                query = {"notice": reason} if reason else {"next": url_path}
                self._redirect(LANDING_PAGE + "?" + urlencode(query))
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
    auto_login_email: str | None = None,
    accounts: AccountsApi | None = None,
    admin_auth: AdminAuth | None = None,
) -> DemoServer:
    """Create the server on 127.0.0.1 (call .serve_forever() to run it).
    `auto_login_email` turns login off for development (see DemoServer)."""
    return DemoServer(("127.0.0.1", port), api, app, sessions, auto_login_email, accounts, admin_auth)
