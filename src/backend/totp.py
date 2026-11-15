"""Authenticator-app codes (TOTP, RFC 6238): the second step of an admin
sign-in that needs no outside account.

The admin adds a key to an authenticator app once (Duo Mobile, Google
Authenticator, 1Password and others all work); from then on the app shows
a six-digit code that changes every 30 seconds, and signing in needs the
current one as well as the password. The key is kept with the admin's
login in the git-ignored credentials file.

A code is accepted for the current 30 seconds and one step either side
(clocks drift), and each code works once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
STEP_SECONDS = 30
WINDOW = 1  # steps of drift allowed either side
ISSUER = "Collectivize"


def new_secret() -> str:
    """A fresh 160-bit key, in the base32 form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.replace(" ", "").upper())
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10 ** DIGITS:0{DIGITS}d}"


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step this code belongs to if it is valid right now, else None."""
    typed = "".join(code.split())
    if len(typed) != DIGITS or not typed.isascii() or not typed.isdigit():
        return None
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - WINDOW, current + WINDOW + 1):
        try:
            if hmac.compare_digest(code_at(secret, step), typed):
                return step
        except (ValueError, TypeError):  # a damaged key never matches
            return None
    return None


def setup_uri(secret: str, account: str) -> str:
    """What an authenticator app's QR scanner or "enter a key" form takes."""
    label = quote(f"{ISSUER}:{account}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"
