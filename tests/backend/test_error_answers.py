"""Whatever goes wrong the browser gets an answer (a page for a person,
JSON for the app), and ranking internals reach platform admins only."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.server_pages import (
    INTERNAL_KEYS,
    error_page,
    has_internals,
    without_internals,
)
from backend.sessions import SessionStore

from .conftest import NOW
from .test_access import call, sign_in

DREW, PAT = "drew.demo@northeastern.edu", "pat.demo@northeastern.edu"
BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
FRONTEND = Path(__file__).resolve().parents[2] / "src/frontend"


@pytest.fixture
def world():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=SessionStore())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv, result
    srv.shutdown()
    srv.server_close()


def browse(url, token=None):
    """Like `call`, but as a browser opening a page (it asks for HTML)."""
    import urllib.error
    import urllib.request

    from backend.sessions import COOKIE_NAME
    headers = dict(BROWSER)
    if token:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
            return response.status, response.headers.get("Content-Type"), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read().decode()


# --- unknown addresses -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/nope", "/no-such-page", "/shared.py", "/src/backend/server.py"])
def test_an_unknown_address_shows_a_page_not_raw_json(world, path):
    base, _, _ = world
    status, kind, body = browse(base + path)
    assert status == 404 and kind.startswith("text/html")
    assert "Page not found" in body and 'href="/"' in body and "{" not in body.split("</style>")[1]
    status, _, raw = call(base + path)  # the app's own requests still get JSON
    assert status == 404 and json.loads(raw) == {"error": "not found"}


def test_the_browsers_icon_request_is_answered(world):
    status, headers, body = call(world[0] + "/favicon.ico")
    assert status == 200 and headers["Content-Type"].startswith("image/svg") and body.startswith(b"<svg")


def test_the_error_page_escapes_its_text():
    page = error_page("<b>x</b>", "a & b").decode()
    assert "<b>x</b>" not in page and "&lt;b&gt;" in page and "a &amp; b" in page


# --- when something breaks -------------------------------------------------------------


@pytest.mark.parametrize(("error", "status", "heading"), [
    (RuntimeError("secret /Users/x/data.db"), 500, "Something went wrong"),
    (sqlite3.OperationalError("database is locked"), 503, "Collectivize is busy"),
])
def test_a_failure_before_the_handler_still_gets_an_answer(world, monkeypatch, error, status, heading):
    base, srv, _ = world
    token, _ = sign_in(base, DREW)

    def broken(_token):
        raise error

    monkeypatch.setattr(srv.sessions, "get", broken)
    code, kind, body = browse(f"{base}/discover", token)
    assert code == status and kind.startswith("text/html") and heading in body
    assert "secret" not in body and "locked" not in body and "Traceback" not in body
    for path, payload in (("/api/me", None), ("/api/rsvp", {})):
        code, _, raw = call(base + path, payload, token=token)
        answer = json.loads(raw)
        assert code == status and set(answer) == {"error"} and "secret" not in answer["error"]
    assert browse(base + "/", token)[0] == status


def test_the_server_keeps_working_after_a_failure(world, monkeypatch):
    base, srv, _ = world
    token, _ = sign_in(base, DREW)
    with monkeypatch.context() as patch:
        patch.setattr(srv.sessions, "get", lambda _token: 1 / 0)
        assert call(f"{base}/api/me", token=token)[0] == 500
    assert call(f"{base}/api/me", token=token)[0] == 200


# --- internals are for admins ----------------------------------------------------------


def test_students_never_receive_scores_distances_or_going_counts(world):
    base, _, _ = world
    student, _ = sign_in(base, DREW)
    for path in ("/api/discover", "/api/find?q=robot", "/api/find", "/api/my-events",
                 "/api/calendar?start=2026-11-01T00:00:00%2B00:00&end=2026-12-01T00:00:00%2B00:00"):
        status, _, raw = call(base + path, token=student)
        assert status == 200 and not has_internals(json.loads(raw)), path
    status, _, raw = call(f"{base}/api/discover", token=student)
    first = json.loads(raw)["items"][0]
    assert {"title", "starts_at", "host", "reasons", "friends_going", "my_rsvp"} <= set(first)
    event_id = first["id"]
    status, _, raw = call(f"{base}/api/rsvp", {"event_id": event_id, "status": "going"}, token=student)
    assert status == 200 and "going_count" not in json.loads(raw) and "is_full" in json.loads(raw)


def test_a_platform_admin_still_gets_them(world):
    base, _, _ = world
    admin, _ = sign_in(base, PAT)
    first = json.loads(call(f"{base}/api/discover", token=admin)[2])["items"][0]
    assert {"score", "going_count", "distance_km"} <= set(first)


def test_my_events_says_when_an_event_is_full(world):
    base, _, _ = world
    student, _ = sign_in(base, DREW)
    events = json.loads(call(f"{base}/api/my-events", token=student)[2])
    assert events["upcoming"] and all(isinstance(e["is_full"], bool) for e in events["upcoming"])
    assert "is_full" in (FRONTEND / "my-events.js").read_text()


def test_without_internals_removes_them_at_any_depth_and_copies():
    payload = {"items": [{"id": 1, "score": 0.9, "host": {"name": "x", "going_count": 4}}], "total": 1}
    assert without_internals(payload) == {"items": [{"id": 1, "host": {"name": "x"}}], "total": 1}
    assert payload["items"][0]["score"] == 0.9 and has_internals(payload)  # the original is untouched
    assert not has_internals({"counts": {"person": 1}, "scores": []}) and INTERNAL_KEYS


# --- wording ---------------------------------------------------------------------------


def test_pages_never_show_developer_wording_or_demo_accounts():
    for path in [*FRONTEND.glob("*.js"), *FRONTEND.glob("*.html")]:
        shown = "\\n".join(line for line in path.read_text().splitlines()
                          if not line.strip().startswith(("//", "*", "/*", "<!--")))
        for phrase in ("serve.py", "terminal", "seed_test_db", "Demo accounts", ".demo@northeastern.edu"):
            assert phrase not in shown, f"{path.name}: {phrase}"
