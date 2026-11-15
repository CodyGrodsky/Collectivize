"""Sign-in sessions (v1.1 spec 4.10, 4.9).

A session is a random token in an HttpOnly cookie, mapped to the person
who signed in. Real email verification is future work, not this.

Remembered: a session given a lifetime (`ttl`) is also written to a file,
so the person stays signed in when the server or their browser restarts,
until it runs out or they sign out. Only a SHA-256 hash of each token is
kept, in memory and in the file, so reading the file gives nobody a
working cookie. Sessions without a lifetime (development auto-login) live
in memory only.

A platform admin's session can also "view as" someone, read-only: while it
does, the app shows that person's view and refuses every change. Each
view-as session is logged (admin, target, time).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_NAME = "collectivize_session"
MAX_SESSIONS = 10_000
MAX_REMEMBERED = 10_000  # ended tokens we still remember the reason for


@dataclass(frozen=True)
class Session:
    person_id: str
    view_as: str | None = None  # set only for platform admins
    expires: float | None = None  # on the store's clock; None = until restart

    @property
    def viewer_id(self) -> str:
        """Whose eyes the app sees through."""
        return self.view_as or self.person_id

    @property
    def read_only(self) -> bool:
        return self.view_as is not None


def _key(token: str) -> str:
    """What is stored instead of the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(
        self,
        audit_path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        path: Path | None = None,
    ) -> None:
        """`path`: where remembered sessions are kept between restarts
        (None: nowhere, they end with the server)."""
        self._sessions: dict[str, Session] = {}  # token hash -> session
        self._clock = clock
        self._path = path
        # Why a token stopped working ("deleted", "signed_out"), so its
        # browser is sent home instead of being signed in again.
        self._ended: dict[str, str] = {}
        self._lock = threading.Lock()
        self._audit_path = audit_path
        self.audit: list[dict[str, str]] = []  # view-as log, newest last
        self._load()

    def create(self, person_id: str, ttl: float | None = None) -> str:
        """`ttl`: seconds until the session ends by itself. With one, the
        session is remembered across restarts."""
        token = secrets.token_urlsafe(32)
        expires = None if ttl is None else self._clock() + ttl
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions[_key(token)] = Session(person_id, expires=expires)
            if expires is not None:
                self._save()
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        key = _key(token)
        with self._lock:
            session = self._sessions.get(key)
            if session and session.expires is not None and self._clock() >= session.expires:
                del self._sessions[key]
                self._remember(key, "expired")
                self._save()
                return None
            return session

    def ended_reason(self, token: str | None) -> str | None:
        """Why this token was ended by someone else, if it was."""
        if not token:
            return None
        with self._lock:
            return self._ended.get(_key(token))

    def _remember(self, key: str, reason: str) -> None:
        if len(self._ended) >= MAX_REMEMBERED:
            self._ended.pop(next(iter(self._ended)))
        self._ended[key] = reason

    def end(self, token: str | None) -> None:
        """Sign out: this browser has to sign in again."""
        if not token:
            return
        with self._lock:
            session = self._sessions.pop(_key(token), None)
            if session is not None and session.expires is not None:
                self._save()

    def end_all(self, person_id: str, reason: str = "signed_out") -> int:
        """Sign this person out everywhere (`reason`: "deleted", or
        "signed_out" by themselves or a platform admin). Each of their
        browsers is sent back to the home page on its next request.
        Returns how many sessions ended."""
        with self._lock:
            keys = [k for k, s in self._sessions.items() if s.person_id == person_id]
            for key in keys:
                del self._sessions[key]
                self._remember(key, reason)
            if keys:
                self._save()
        return len(keys)

    def count_for(self, person_id: str) -> int:
        with self._lock:
            return sum(s.person_id == person_id for s in self._sessions.values())

    def view_as(self, token: str, target_id: str | None, now: datetime) -> Session:
        """Start (target) or stop (None) viewing as someone. The caller has
        already checked that this session's person is a platform admin."""
        key = _key(token)
        with self._lock:
            session = self._sessions[key]
            updated = replace(session, view_as=target_id)
            self._sessions[key] = updated
        entry = {
            "at": now.isoformat(),
            "admin": session.person_id,
            "action": "start" if target_id else "stop",
            "target": target_id or session.view_as or "",
        }
        self._log(entry)
        return updated

    # --- remembered sessions -------------------------------------------------

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))["sessions"]
            now = self._clock()
            for key, entry in saved.items():
                expires = float(entry["expires"])
                if expires > now and len(key) == 64:
                    self._sessions[key] = Session(str(entry["person_id"]), expires=expires)
        except (OSError, KeyError, TypeError, ValueError, AttributeError):
            # An unreadable file only means everyone signs in again.
            log.warning("couldn't read the saved sessions; starting with none")
            self._sessions.clear()

    def _save(self) -> None:
        """Called with the lock held. View-as state is never saved: after a
        restart an admin is back to their own view."""
        if self._path is None:
            return
        payload = {"sessions": {
            key: {"person_id": s.person_id, "expires": s.expires}
            for key, s in self._sessions.items() if s.expires is not None
        }}
        temporary = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            os.replace(temporary, self._path)  # all or nothing
        except OSError:
            log.warning("couldn't save sessions", exc_info=True)

    def _log(self, entry: dict[str, str]) -> None:
        self.audit.append(entry)
        log.info("view-as %s: admin %s, target %s", entry["action"], entry["admin"], entry["target"])
        if self._audit_path is None:
            return
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")
        except OSError:
            log.warning("couldn't write the view-as log", exc_info=True)
