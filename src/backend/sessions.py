"""Demo sign-in sessions (v1.1 spec 4.10, 4.9).

A session is a random token in an HttpOnly cookie, mapped in memory to the
person who signed in. Sessions end when the server restarts; real email
verification is future work, not this.

A platform admin's session can also "view as" someone, read-only: while it
does, the app shows that person's view and refuses every change. Each
view-as session is logged (admin, target, time).
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_NAME = "collectivize_session"
MAX_SESSIONS = 10_000


@dataclass(frozen=True)
class Session:
    person_id: str
    view_as: str | None = None  # set only for platform admins

    @property
    def viewer_id(self) -> str:
        """Whose eyes the app sees through."""
        return self.view_as or self.person_id

    @property
    def read_only(self) -> bool:
        return self.view_as is not None


class SessionStore:
    def __init__(self, audit_path: Path | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._audit_path = audit_path
        self.audit: list[dict[str, str]] = []  # view-as log, newest last

    def create(self, person_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions[token] = Session(person_id)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    def end(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def view_as(self, token: str, target_id: str | None, now: datetime) -> Session:
        """Start (target) or stop (None) viewing as someone. The caller has
        already checked that this session's person is a platform admin."""
        with self._lock:
            session = self._sessions[token]
            updated = replace(session, view_as=target_id)
            self._sessions[token] = updated
        entry = {
            "at": now.isoformat(),
            "admin": session.person_id,
            "action": "start" if target_id else "stop",
            "target": target_id or session.view_as or "",
        }
        self._log(entry)
        return updated

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
