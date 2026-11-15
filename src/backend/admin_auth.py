"""The admin sign-in: a username and password, checked against a local
credentials file that is never committed.

The file (`config/admin.local.json`, git-ignored, written by
`scripts/set_admin_password.py`) lists the admins:

    {"admins": [{"username": "...", "email": "<their account's email>",
                 "password_hash": "scrypt$...", "role": "owner" | "admin"}]}

`role: "owner"` marks the account that manages the other admins
(admin_policy.py). It can only be set here, on this machine, never from
the app.

- Passwords are never stored: only a salted scrypt hash. A file holding a
  plain `password` is refused, so one can't be left lying around.
- A wrong username and a wrong password look the same from outside, and
  cost the same time (no hint about which usernames exist).
- Five wrong passwords for a username lock it for fifteen minutes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.config import PROJECT_ROOT

CREDENTIALS_PATH = PROJECT_ROOT / "config" / "admin.local.json"
MIN_PASSWORD = 12
MAX_PASSWORD = 200
MAX_USERNAME = 64
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_BYTES = 2**14, 8, 1, 32


class AdminAuthError(ValueError):
    """The credentials file is missing something or unsafe."""


class LockedOutError(Exception):
    """Too many wrong passwords for this username; try again later."""


@dataclass(frozen=True)
class AdminCredential:
    username: str
    email: str  # the platform-admin account this signs in to
    password_hash: str
    role: str = "admin"  # or "owner"


def hash_password(password: str, salt: bytes | None = None) -> str:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AdminAuthError(f"the password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                         dklen=KEY_BYTES)
    encode = lambda raw: base64.b64encode(raw).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${encode(salt)}${encode(key)}"


def check_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, key = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked when the username is unknown, so that costs the same time.
_DUMMY_HASH = hash_password("not-a-real-password")


class AdminAuth:
    def __init__(
        self,
        credentials: tuple[AdminCredential, ...] = (),
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._by_name = {c.username.casefold(): c for c in credentials}
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    @property
    def is_set_up(self) -> bool:
        return bool(self._by_name)

    @classmethod
    def load(cls, path: Path = CREDENTIALS_PATH) -> AdminAuth:
        """The admins in the credentials file; none if there is no file yet."""
        if not path.exists():
            return cls()
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["admins"]
            if any("password" in entry for entry in entries):
                raise AdminAuthError(
                    f"{path.name} holds a plain password: run scripts/set_admin_password.py instead"
                )
            return cls(tuple(
                AdminCredential(str(e["username"]), str(e["email"]).lower(),
                                str(e["password_hash"]), _role(e.get("role", "admin")))
                for e in entries
            ))
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, AdminAuthError):
                raise
            raise AdminAuthError(f"{path.name} isn't a valid admin credentials file") from None

    @property
    def owner_emails(self) -> frozenset[str]:
        return frozenset(c.email for c in self._by_name.values() if c.role == "owner")

    @property
    def emails(self) -> frozenset[str]:
        return frozenset(c.email for c in self._by_name.values())

    def credential(self, username: str) -> AdminCredential | None:
        return self._by_name.get(username.strip().casefold())

    def verify(self, username: str, password: str) -> str | None:
        """The admin account's email if the username and password match,
        else None. Raises LockedOutError while the username is locked."""
        name = username.strip().casefold()[:MAX_USERNAME]
        credential = self._by_name.get(name)
        if credential is not None and self._locked(name):
            raise LockedOutError
        matches = check_password(password[:MAX_PASSWORD],
                                 credential.password_hash if credential else _DUMMY_HASH)
        if credential is None:
            return None
        with self._lock:
            if matches:
                self._failures.pop(name, None)
            else:
                self._failures.setdefault(name, []).append(self._clock())
        return credential.email if matches else None

    def _locked(self, name: str) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(name, []) if self._clock() - t < LOCK_SECONDS]
            self._failures[name] = recent
            return len(recent) >= MAX_FAILURES


def _role(value: object) -> str:
    if value not in ("admin", "owner"):
        raise AdminAuthError('an admin\'s role must be "admin" or "owner"')
    return str(value)


def save_credential(credential: AdminCredential, path: Path = CREDENTIALS_PATH) -> None:
    """Add or replace one admin in the credentials file (readable only by you)."""
    existing = dict(AdminAuth.load(path)._by_name) if path.exists() else {}
    existing[credential.username.casefold()] = credential
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"admins": [
        {"username": c.username, "email": c.email, "password_hash": c.password_hash,
         "role": c.role}
        for c in existing.values()
    ]}
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
