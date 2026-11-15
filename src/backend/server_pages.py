"""What the server sends when there is no ordinary answer, and what it
never sends to ordinary users.

- `error_page`: a small self-contained page ("Page not found", "Something
  went wrong") for a person's browser, instead of raw JSON or nothing. It
  needs no other file, so it still works when something is broken.
- `AnswerMixin` (mixed into server.py's RequestHandler) makes sure every
  request gets one of those answers, whatever fails.
- `without_internals`: removes fields that exist for ranking and debugging
  (`score`, `distance_km`) or that the app chose not to show
  (`going_count`) from an API answer. Only platform admins get them.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from html import escape
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from backend.audit import ACTING_LOGIN

log = logging.getLogger(__name__)

ALLOWED_METHODS = "GET, HEAD, POST, OPTIONS"

# Never sent to anyone but a platform admin, whichever route produced them.
INTERNAL_KEYS = frozenset({"score", "distance_km", "going_count"})

NOT_FOUND = ("Page not found", "That page doesn't exist, or it has moved.")
BROKEN = ("Something went wrong", "That's on our side, not yours. Please try again in a moment.")
BUSY = ("Collectivize is busy", "Please try again in a moment.")

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>{heading} · Collectivize</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f5f4f0;
         color: #202b29; font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 420px; padding: 32px 20px; text-align: center; }}
  img {{ width: 96px; height: 96px; }}
  h1 {{ margin: 18px 0 6px; font-size: 26px; letter-spacing: -.01em; }}
  p {{ margin: 0 0 22px; color: #5f6b67; }}
  a {{ display: inline-block; padding: 10px 18px; border-radius: 10px; background: #202b29;
      color: #fff; font-weight: 600; text-decoration: none; }}
  a:focus-visible {{ outline: 2px solid #266956; outline-offset: 2px; }}
</style>
</head>
<body>
<main>
  <img src="/public/brand/logo.svg" alt="">
  <h1>{heading}</h1>
  <p>{text}</p>
  <a href="/">Go to Collectivize</a>
</main>
</body>
</html>
"""


def error_page(heading: str, text: str) -> bytes:
    return _PAGE.format(heading=escape(heading), text=escape(text)).encode()


def page_for(status: HTTPStatus) -> tuple[str, str]:
    if status == HTTPStatus.NOT_FOUND:
        return NOT_FOUND
    return BUSY if status == HTTPStatus.SERVICE_UNAVAILABLE else BROKEN


def has_internals(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in INTERNAL_KEYS or has_internals(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_internals(item) for item in value)
    return False


def without_internals(value: Any) -> Any:
    """A copy of an API answer with every INTERNAL_KEYS field removed."""
    if isinstance(value, dict):
        return {key: without_internals(item) for key, item in value.items()
                if key not in INTERNAL_KEYS}
    if isinstance(value, list):
        return [without_internals(item) for item in value]
    return value


class AnswerMixin:
    """Needs the handler's `headers`, `path`, `server`, `_json`, `_cookie`,
    `_is_platform_admin` and `_security_headers`."""

    def _guarded(self, respond: Callable[[], None]) -> None:
        """Whatever goes wrong, the browser gets an answer: never a dropped
        connection, and never the details."""
        ACTING_LOGIN.set("")  # nobody yet: set once the session is known
        try:
            respond()
        except (BrokenPipeError, ConnectionResetError):
            log.info("client left before the answer was sent: %s", urlsplit(self.path).path)
            self.close_connection = True
        except sqlite3.OperationalError:  # e.g. "database is locked" by another save
            log.warning("database busy", exc_info=True)
            self._fail(HTTPStatus.SERVICE_UNAVAILABLE, "busy right now, please try again")
        except Exception:
            log.exception("request failed: %s", urlsplit(self.path).path)
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "something went wrong")

    def _fail(self, status: HTTPStatus, message: str) -> None:
        """An error answer: a page for a person's browser, JSON for the app."""
        try:
            if not urlsplit(self.path).path.startswith("/api/") and self._wants_page():
                self._page(status)
            else:
                self._json(status, {"error": message})
        except Exception:  # noqa: BLE001 (the connection itself is gone: nothing more to do)
            self.close_connection = True

    def _wants_page(self) -> bool:
        return "text/html" in (self.headers.get("Accept") or "")

    def _page(self, status: HTTPStatus) -> None:
        data = error_page(*page_for(status))
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self._send_body(data)

    def _send_body(self, data: bytes) -> None:
        if self.command != "HEAD":  # HEAD: the same headers, no body
            self.wfile.write(data)

    # --- the other HTTP methods ------------------------------------------------

    def do_HEAD(self) -> None:
        self._guarded(self._get)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", ALLOWED_METHODS)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _not_allowed(self) -> None:
        data = b'{"error": "that method isn\'t supported here"}'
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", ALLOWED_METHODS)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    do_PUT = do_DELETE = do_PATCH = _not_allowed

    def _for_viewer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ranking scores, distances and going counts are for platform
        admins only, whichever route produced them."""
        if not has_internals(payload):
            return payload
        if self._is_platform_admin(self.server.sessions.get(self._cookie())):
            return payload
        return without_internals(payload)
