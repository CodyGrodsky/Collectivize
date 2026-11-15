"""Proving a Northeastern address is yours: a six-digit code sent to it.

Signing in is two steps. Typing an address asks for a code (`start`);
typing the code back (`check`) is what signs you in. So an address alone
opens nothing: you need to be able to read its mailbox.

Rules:
- A code works once, for CODE_SECONDS, and is replaced by a newer one.
- MAX_TRIES wrong guesses throw the code away (a six-digit code can't be
  guessed in five tries).
- One address can ask again after RESEND_SECONDS, at most
  MAX_SENDS_PER_HOUR times an hour; the whole server sends at most
  MAX_SENDS_PER_MINUTE a minute, so nobody can use it to flood mailboxes.
- Only a hash of the code is kept, and only in memory.
- `start` answers the same way whether or not the address has an account.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from backend.mailer import ConsoleMailer, Mailer, MailError, is_demo_address

CODE_DIGITS = 6
CODE_SECONDS = 10 * 60
MAX_TRIES = 5
RESEND_SECONDS = 30
MAX_SENDS_PER_HOUR = 5
MAX_SENDS_PER_MINUTE = 60
MAX_PENDING = 10_000


class VerificationError(Exception):
    """Why a code wasn't sent or accepted. The message is safe to show."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Pending:
    digest: bytes
    expires: float
    tries: int = 0


def _digest(address: str, code: str) -> bytes:
    return hashlib.sha256(f"{address}:{code}".encode()).digest()


class EmailVerifier:
    def __init__(self, mailer: Mailer, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._mailer = mailer
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._sent: dict[str, deque[float]] = {}  # address -> when codes were sent
        self._all_sent: deque[float] = deque()

    def is_emailed(self, address: str) -> bool:
        """False when the code is shown in the server's terminal instead:
        mail isn't set up, or the address is a fictional demo account."""
        return not isinstance(self._mailer, ConsoleMailer) and not is_demo_address(address)

    def start(self, address: str) -> None:
        """Send a fresh code to this (already validated, canonical) address."""
        now = self._clock()
        with self._lock:
            sent = self._sent.setdefault(address, deque())
            while sent and now - sent[0] > 3600:
                sent.popleft()
            while self._all_sent and now - self._all_sent[0] > 60:
                self._all_sent.popleft()
            if sent and now - sent[-1] < RESEND_SECONDS:
                raise VerificationError(HTTPStatus.TOO_MANY_REQUESTS,
                                        "a code was just sent: wait a moment before asking again")
            if len(sent) >= MAX_SENDS_PER_HOUR or len(self._all_sent) >= MAX_SENDS_PER_MINUTE:
                raise VerificationError(HTTPStatus.TOO_MANY_REQUESTS,
                                        "too many codes requested: try again later")
            code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
            if len(self._pending) >= MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[address] = _Pending(_digest(address, code), now + CODE_SECONDS)
            sent.append(now)
            self._all_sent.append(now)
        try:
            self._mailer.send_code(address, code, CODE_SECONDS // 60)
        except MailError:
            with self._lock:
                self._pending.pop(address, None)
            raise VerificationError(HTTPStatus.SERVICE_UNAVAILABLE,
                                    "the code couldn't be emailed right now: try again soon") from None

    def check(self, address: str, code: str) -> None:
        """Returns only if this is the address's current code; then the code
        is spent. Raises VerificationError otherwise."""
        with self._lock:
            pending = self._pending.get(address)
            if pending is None or self._clock() >= pending.expires:
                self._pending.pop(address, None)
                raise VerificationError(HTTPStatus.UNAUTHORIZED,
                                        "that code has run out: ask for a new one")
            if not hmac.compare_digest(pending.digest, _digest(address, code.strip())):
                pending.tries += 1
                if pending.tries >= MAX_TRIES:
                    del self._pending[address]
                    raise VerificationError(HTTPStatus.UNAUTHORIZED,
                                            "too many wrong codes: ask for a new one")
                raise VerificationError(HTTPStatus.UNAUTHORIZED, "that code isn't right")
            del self._pending[address]  # works once
