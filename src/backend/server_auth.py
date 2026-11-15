"""Signing in and staying signed in: the part of the request handler that
deals with cookies, sessions and the two sign-in routes (server.py mixes
this into its RequestHandler).

One sign-in screen for everyone (POST /api/signin with what was typed):
- an ordinary Northeastern email signs in at once;
- an admin's username, or an admin account's email, is answered with
  {"admin": true}: the page then asks for the password and sends both to
  POST /api/admin/signin. If Duo is set up (duo.py) that request also
  needs Duo's approval: a push to the admin's phone, or a `passcode`.

Remembered: a sign-in lasts REMEMBER_SECONDS (an admin's,
ADMIN_SESSION_SECONDS) across browser and server restarts, until the
person signs out. Signing out, an account being deleted, or "sign out
everywhere" ends it, and the next visit starts at the sign-in screen.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from backend.admin_auth import MAX_EMAIL, MAX_PASSWORD, LockedOutError
from backend.demo_api import ApiError
from backend.duo import MAX_PASSCODE, DuoError
from backend.objects.base import utc_now
from backend.sessions import COOKIE_NAME, Session

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 4096
REMEMBER_SECONDS = 30 * 24 * 60 * 60  # how long a sign-in is remembered
ADMIN_SESSION_SECONDS = 8 * 60 * 60  # an admin sign-in ends sooner
COOKIE_FLAGS = "Path=/; HttpOnly; SameSite=Lax"


class AuthHandlerMixin:
    """Needs the handler's `server`, `headers`, `_json` and `_read_json`."""

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
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={token}; {COOKIE_FLAGS}")
            self._pending_cookie = None
        elif getattr(self, "_clear_cookie", False):
            self.send_header("Set-Cookie", f"{COOKIE_NAME}=; {COOKIE_FLAGS}; Max-Age=0")
        self._clear_cookie = False
        super().end_headers()

    # --- signing in ----------------------------------------------------------------

    def _sign_in(self) -> None:
        """POST /api/signin {"email"}: what was typed on the sign-in screen."""
        auth = self.server.admin_auth
        try:
            body = self._read_json(MAX_BODY_BYTES)
            typed = body.get("email")
            if auth and isinstance(typed, str) and len(typed) <= MAX_EMAIL and auth.knows(typed):
                self._json(HTTPStatus.OK, {"admin": True})  # next: the password
                return
            person_id = self.server.app.sign_in(body)
        except ApiError as error:
            self._json(error.status, {"error": error.message})
            return
        except Exception:
            log.exception("sign-in failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "something went wrong"})
            return
        if auth and self.server.is_platform_admin(person_id):
            # Admin accounts open only with the admin password (and Duo).
            self._json(HTTPStatus.OK, {"admin": True})
            return
        self._start_session(person_id, REMEMBER_SECONDS)

    def _start_session(self, person_id: str, ttl: float) -> None:
        self.server.sessions.end(self._cookie())
        token = self.server.sessions.create(person_id, ttl)
        session = self.server.sessions.get(token)
        self._json(HTTPStatus.OK, self._me(session), cookie=token, max_age=int(ttl))

    def _admin_sign_in(self) -> None:
        """POST /api/admin/signin {"username", "password", "passcode"?}.
        `username` is the admin username or the admin account's email. A
        wrong name and a wrong password get the same answer."""
        auth, refused = self.server.admin_auth, {"error": "wrong username or password"}
        if auth is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            body = self._read_json(MAX_BODY_BYTES)
            username, password = body.get("username"), body.get("password")
            passcode = body.get("passcode") or None
            if not (isinstance(username, str) and isinstance(password, str)
                    and 0 < len(username) <= MAX_EMAIL and 0 < len(password) <= MAX_PASSWORD
                    and (passcode is None or (isinstance(passcode, str)
                                              and len(passcode) <= MAX_PASSCODE))):
                self._json(HTTPStatus.UNAUTHORIZED, refused)
                return
            credential = auth.check(username, password)
            person_id = self.server.app.admin_account_id(credential.email) if credential else None
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
        audit = self.server.accounts.audit
        if person_id is None:
            audit.record("", "sign-in failed", "", utc_now())
            self._json(HTTPStatus.UNAUTHORIZED, refused)
            return
        if self.server.duo is not None:  # the second factor, admins only
            try:
                self.server.duo.approve(credential.duo_username or credential.username, passcode)
            except DuoError as error:
                audit.record(person_id, "sign-in failed", person_id, utc_now(), "duo")
                self._json(HTTPStatus.UNAUTHORIZED, {
                    "error": error.message, "needs_passcode": error.needs_passcode, "duo": True,
                })
                return
        audit.record(person_id, "sign-in", person_id, utc_now(),
                     "password + duo" if self.server.duo else "password")
        self._start_session(person_id, ADMIN_SESSION_SECONDS)
