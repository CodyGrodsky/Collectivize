"""The site's addresses, and which file each one serves.

People see short addresses: `/discover`, `/my-events`, `/profile`,
`/admin`, with the sign-in screen at `/`. Scripts and stylesheets are at
the top level too (`/shared.js`). Nothing in an address says where a file
lives in the repository, and a file is only ever served through this
table, so the old `/src/frontend/...` addresses can't be used to reach
one directly (they redirect to the new address instead).

Images are served from `/app/` and `/public/` as before.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

FRONTEND = "/src/frontend"
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "src" / "frontend"

LANDING_PAGE = "/"  # the sign-in screen, for anyone signed out
HOME_PAGE = "/home"  # the same page at a fixed address (signed-in home, admin entrance)
SIGNED_IN_HOME = "/discover"
ADMIN_PAGE = "/admin"
ADMIN_LOGIN_PAGE = "/home?admin=1"  # the sign-in screen, shown even to someone signed in
INVITE_PAGE = "/invite"
FAVICON = "/app/icon.svg"
# Pages a signed-out visitor may open (v1.1 spec 4.10).
PUBLIC_PAGES = frozenset({LANDING_PAGE, HOME_PAGE, INVITE_PAGE})
# Served to platform admins only: the page and the scripts that drive it.
ADMIN_ONLY = frozenset({ADMIN_PAGE, "/admin.js", "/admin-team.js"})
# Pages that have moved (their query string is carried over).
MOVED = {"/calendar": "/my-events?view=calendar"}

PAGE = re.compile(r"/[a-z0-9]+(?:-[a-z0-9]+)*")
ASSET = re.compile(r"/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:js|css)")
IMAGE_DIRS = ("/app/", "/public/")

CONTENT_SECURITY_POLICY = "; ".join((  # noqa: FLY002 (one rule per line)
    "default-src 'self'",
    # No inline scripts. The one outside source is the three.js library
    # the home page's 3D logo imports.
    "script-src 'self' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))


def with_query(address: str, query: str) -> str:
    """`address` with the request's query string added to its own."""
    if not query:
        return address
    path, _, own = address.partition("?")
    merged = dict(parse_qsl(query, keep_blank_values=True)) | dict(parse_qsl(own))
    return f"{path}?{urlencode(merged)}"


def moved_to(path: str, query: str = "") -> str | None:
    """Where an old address lives now, or None if it isn't an old address:
    `/src/frontend/discover.html?x=1` and `/discover.html?x=1` are both
    `/discover?x=1`."""
    if path.startswith(FRONTEND + "/"):
        path = "/" + path.removeprefix(FRONTEND + "/")
        if "/" in path[1:]:
            return None
        if not path.endswith(".html"):
            return with_query(path, query) if ASSET.fullmatch(path) else None
    if path.endswith(".html"):
        name = path.removesuffix(".html")
        if not PAGE.fullmatch(name):
            return None
        path = HOME_PAGE if name == "/index" else name
    elif path not in MOVED:
        return None
    return with_query(MOVED.get(path, path), query)


def is_page(path: str) -> bool:
    return page_file(path) is not None


def page_file(path: str) -> str | None:
    """The HTML file behind a page address."""
    if path in (LANDING_PAGE, HOME_PAGE):
        return f"{FRONTEND}/index.html"
    if not PAGE.fullmatch(path) or path == "/index":
        return None
    name = f"{path[1:]}.html"
    return f"{FRONTEND}/{name}" if (FRONTEND_DIR / name).is_file() else None


def file_for(path: str) -> str | None:
    """The file to serve for an address (checked again by resolve_static),
    or None when the address names nothing."""
    page = page_file(path)
    if page is not None:
        return page
    if ASSET.fullmatch(path):
        return f"{FRONTEND}{path}"
    if path.startswith(IMAGE_DIRS):
        return path
    return None
