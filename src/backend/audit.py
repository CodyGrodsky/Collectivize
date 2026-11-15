"""The platform admin audit log: who did what to which account, and when.

Append-only. Each entry records the admin, the action, the account it
touched and the *names* of the fields changed, never their values, so the
log itself holds no profile content. Kept in memory for the admin page and
appended to a JSON-lines file when a path is given.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MAX_KEPT = 1000  # entries held in memory; the file keeps everything


@dataclass(frozen=True)
class AuditEntry:
    at: str  # ISO time
    admin: str  # person id of the platform admin
    action: str  # e.g. "update", "create", "delete", "sign-out"
    target: str  # person id of the account
    detail: str = ""  # field names changed; never values


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []

    def record(
        self, admin_id: str, action: str, target_id: str, now: datetime, detail: str = ""
    ) -> AuditEntry:
        entry = AuditEntry(now.isoformat(), admin_id, action, target_id, detail)
        with self._lock:
            self._entries.append(entry)
            del self._entries[:-MAX_KEPT]
            self._write(entry)
        log.info("admin %s: %s %s %s", admin_id, action, target_id, detail)
        return entry

    def entries(self) -> list[dict[str, str]]:
        """Oldest first."""
        with self._lock:
            return [asdict(entry) for entry in self._entries]

    def _write(self, entry: AuditEntry) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(asdict(entry)) + "\n")
        except OSError:
            log.warning("couldn't write the admin audit log", exc_info=True)
