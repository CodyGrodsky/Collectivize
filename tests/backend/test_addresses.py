"""Short addresses that don't show the repository's layout, admin scripts
for admins only, a Content-Security-Policy, and the other HTTP methods."""

import http.client
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.server_routes import CONTENT_SECURITY_POLICY, file_for, moved_to, page_file
from backend.sessions import COOKIE_NAME, SessionStore

from .conftest import NOW
from .test_access import call, sign_in

DREW, PAT = "drew.demo@northeastern.edu", "pat.demo@northeastern.edu"
FRONTEND = Path(__file__).resolve().parents[2] / "src/frontend"
PAGES = sorted(p.stem for p in FRONTEND.glob("*.html") if p.stem not in ("index", "admin", "calendar"))


@pytest.fixture(scope="module")
def world():
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=SessionStore())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, sign_in(base, DREW)[0], sign_in(base, PAT)[0]
    srv.shutdown()
    srv.server_close()


def request(base, method, path, token=None):
    connection = http.client.HTTPConnection(urlsplit(base).netloc)
    connection.request(method, path, headers={"Cookie": f"{COOKIE_NAME}={token}"} if token else {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), body


# --- addresses -------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_a_short_address(world, page):
    base, student, _ = world
    status, headers, body = call(f"{base}/{page}", token=student)
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"<html" in body


@pytest.mark.parametrize(("old", "new"), [
    ("/src/frontend/discover.html", "/discover"),
    ("/src/frontend/profile.html?id=abc", "/profile?id=abc"),
    ("/discover.html?date=2026-11-20", "/discover?date=2026-11-20"),
    ("/src/frontend/index.html?next=%2Fprofile", "/home?next=%2Fprofile"),
    ("/src/frontend/shared.js", "/shared.js"),
    ("/calendar?date=2026-11-20", "/my-events?date=2026-11-20&view=calendar"),
    ("/src/frontend/calendar.html", "/my-events?view=calendar"),
])
def test_old_addresses_redirect_to_the_new_ones(world, old, new):
    base, student, _ = world
    status, headers, _ = call(base + old, token=student)
    assert status == 302 and headers["Location"] == new
    assert moved_to(*old.partition("?")[::2]) == new


@pytest.mark.parametrize("path", [
    "/src/frontend/", "/src/frontend", "/src%2Ffrontend/shared.js", "/src/frontend/../backend/server.py",
    "/src/frontend/sub/x.js", "/index", "/Discover", "/discover/", "/shared.py", "/.env", "/data/test.db",
    "/config/admin.local.json", "/app/../data/test.db", "/public/%2e%2e/data/test.db", "/%61pp/icon.svg",
])
def test_nothing_else_reaches_a_file(world, path):
    base, student, _ = world
    assert call(base + path, token=student)[0] == 404, path


def test_the_address_table_only_names_real_frontend_files():
    assert page_file("/") == page_file("/home") == "/src/frontend/index.html"
    assert page_file("/discover") == "/src/frontend/discover.html"
    assert page_file("/index") is None and page_file("/nope") is None and page_file("/../x") is None
    assert file_for("/shared.js") == "/src/frontend/shared.js" and file_for("/app/icon.svg") == "/app/icon.svg"
    assert file_for("/server.py") is None and file_for("/src/backend/server.py") is None
    assert moved_to("/discover") is None and moved_to("/src/backend/server.html") is None


def test_no_page_or_script_mentions_the_old_addresses():
    for path in [*FRONTEND.glob("*.js"), *FRONTEND.glob("*.html")]:
        text = path.read_text()
        assert "src/frontend" not in text and "../../" not in text, path.name
        assert "scripts/" not in text.replace("scripts/set_admin_password.py", ""), path.name  # (the owner's own hint)
        links = re.findall(r"""["'`]([a-z-]+\.html)""", text)
        assert not links, f"{path.name}: {links}"


def test_signed_in_links_resolve_from_the_top_level(world):
    base, student, _ = world
    page = call(f"{base}/discover", token=student)[2].decode()
    for ref in re.findall(r'(?:src|href)="([^"#]+)"', page):
        assert not ref.startswith(("http", "//", "..")), ref
        assert call(f"{base}/{ref.lstrip('/')}", token=student)[0] == 200, ref


# --- admin scripts ---------------------------------------------------------------------


@pytest.mark.parametrize("script", ["/admin.js", "/admin-team.js"])
def test_admin_scripts_are_served_to_platform_admins_only(world, script):
    base, student, admin = world
    assert call(base + script)[0] == 404  # signed out
    assert call(base + script, token=student)[0] == 404
    status, _, body = call(base + script, token=admin)
    assert status == 200 and b"use strict" in body
    assert call(f"{base}/src/frontend{script}", token=student)[0] == 302  # only to the gated address
    assert call(f"{base}/shared.js")[0] == 200  # ordinary scripts stay public (the sign-in screen needs them)


# --- headers ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/discover", "/api/session", "/shared.js", "/nope", "/app/icon.svg"])
def test_every_answer_carries_the_security_headers(world, path):
    base, student, _ = world
    headers = call(base + path, token=student)[1]
    assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY and "unsafe-eval" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY.split("script-src")[1].split(";")[0]
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["X-Frame-Options"] == "DENY"
    assert "geolocation=()" in headers["Permissions-Policy"]


def test_no_page_has_an_inline_script():
    for path in FRONTEND.glob("*.html"):
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), path.name
        assert not re.search(r"\son[a-z]+\s*=", text), path.name  # onclick= and friends


# --- methods ---------------------------------------------------------------------------


def test_head_gives_the_headers_without_the_body(world):
    base, student, _ = world
    for path in ("/discover", "/api/me", "/shared.js", "/nope"):
        get_status, get_headers, get_body = request(base, "GET", path, student)
        status, headers, body = request(base, "HEAD", path, student)
        assert status == get_status and body == b"" and get_body
        assert headers["Content-Length"] == get_headers["Content-Length"]
        assert headers["Content-Type"] == get_headers["Content-Type"]


def test_options_lists_what_is_allowed(world):
    status, headers, body = request(world[0], "OPTIONS", "/api/me")
    assert status == 204 and headers["Allow"] == "GET, HEAD, POST, OPTIONS" and body == b""


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_other_methods_are_refused_plainly(world, method):
    base, student, _ = world
    status, headers, body = request(base, method, "/api/me", student)
    assert status == 405 and headers["Allow"] == "GET, HEAD, POST, OPTIONS"
    assert set(json.loads(body)) == {"error"} and b"<html" not in body.lower()
    assert request(base, "GET", "/api/me", student)[0] == 200  # nothing was changed or broken
