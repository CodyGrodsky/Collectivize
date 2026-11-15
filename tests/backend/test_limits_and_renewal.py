"""Connection requests are limited, and a remembered sign-in renews with
use (an admin's does not); the cookie is Secure over https."""

import json
import threading
from contextlib import contextmanager
from http import HTTPStatus

import pytest

from backend.admin_auth import AdminAuth, AdminCredential, hash_password
from backend.app_api import CONNECT_REQUESTS_PER_HOUR, AppApi
from backend.config import Environment
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.graph.connections import MAX_OPEN_REQUESTS
from backend.objects import DomainRuleError, Person, Provenance, ProvenanceMethod
from backend.rate_limit import RateLimiter
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.server_auth import ADMIN_SESSION_SECONDS, REMEMBER_SECONDS, cookie_flags
from backend.sessions import RENEW_AFTER_SECONDS, SessionStore

from .conftest import NOW
from .test_access import call

DREW, PAT = "drew.demo@northeastern.edu", "pat.demo@northeastern.edu"
DAY = 24 * 60 * 60


def strangers(graph, count):
    """New people nobody is connected to yet."""
    provenance = Provenance(created_by=None, method=ProvenanceMethod.SELF_CLAIM, created_at=NOW)
    return [graph.store.save_node(Person(display_name=f"Stranger {n}", provenance=provenance)).id
            for n in range(count)]


@pytest.fixture
def world():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    return AppApi(factory, clock=lambda: NOW), graph, result


# --- connection requests ---------------------------------------------------------------


def test_only_so_many_requests_can_be_waiting_at_once(world):
    _, graph, _ = world
    asker, *people = strangers(graph, MAX_OPEN_REQUESTS + 3)
    for person in people[:MAX_OPEN_REQUESTS]:
        graph.connections.request(asker, person, NOW)
    with pytest.raises(DomainRuleError) as full:
        graph.connections.request(asker, people[-1], NOW)
    assert "waiting for an answer" in str(full.value)
    assert graph.connections.between(asker, people[-1]) is None  # nothing was saved
    # An answer frees a place, and an accepted connection doesn't count as waiting.
    graph.connections.accept(graph.connections.between(asker, people[0]).id, people[0])
    graph.connections.request(asker, people[-1], NOW)
    with pytest.raises(DomainRuleError):
        graph.connections.request(asker, people[-2], NOW)
    # Other people are unaffected.
    graph.connections.request(people[1], people[2], NOW)


def test_a_burst_of_requests_is_slowed_down(world):
    app, graph, result = world
    drew = result.people["drew"]
    people = strangers(graph, CONNECT_REQUESTS_PER_HOUR + 1)
    for person in people[:CONNECT_REQUESTS_PER_HOUR]:
        try:
            app.connect(drew, {"person_id": person})
        except DomainRuleError:
            break  # the waiting cap can come first; either way requests stop
    with pytest.raises((ApiError, DomainRuleError)) as stopped:
        app.connect(drew, {"person_id": people[-1]})
    if isinstance(stopped.value, ApiError):
        assert stopped.value.status == HTTPStatus.TOO_MANY_REQUESTS
    # Answering someone else's request is not a new request: never limited.
    asker = strangers(graph, 1)[0]
    graph.connections.request(asker, drew, NOW)
    assert app.connect(drew, {"person_id": asker})["connection"] == "connected"


def test_the_limiter_counts_per_person_and_forgets_after_the_window():
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")  # someone else
    now[0] = 61
    assert limiter.allow("a")


# --- remembered sign-ins renew with use ------------------------------------------------


def test_a_renewing_session_is_extended_once_a_day_of_use(tmp_path):
    now = [1000.0]
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path, clock=lambda: now[0])
    token = store.create("drew", 30 * DAY, renew=True)
    assert store.refresh(token) is None  # just made
    now[0] += RENEW_AFTER_SECONDS - 1
    assert store.refresh(token) is None  # not a day yet: nothing rewritten
    now[0] += 2
    assert store.refresh(token) == 30 * DAY and store.refresh(token) is None
    assert store.get(token).expires == now[0] + 30 * DAY
    # The new end date survives a restart, and so does the renewing.
    restarted = SessionStore(path=path, clock=lambda: now[0])
    assert restarted.get(token).expires == now[0] + 30 * DAY
    now[0] += 29 * DAY
    assert restarted.refresh(token) == 30 * DAY  # used on day 29: good for 30 more
    now[0] += 30 * DAY + 1
    assert restarted.get(token) is None  # unused for 30 days: signed out


def test_an_admin_session_ends_on_schedule_however_much_it_is_used():
    now = [0.0]
    store = SessionStore(clock=lambda: now[0])
    token = store.create("admin", 8 * 3600)
    now[0] = 7 * 3600
    assert store.refresh(token) is None and store.get(token) is not None
    now[0] = 8 * 3600 + 1
    assert store.get(token) is None
    assert store.refresh("no-such-token") is None and store.refresh(None) is None


@contextmanager
def running(sessions, admin_auth=None):
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=sessions, admin_auth=admin_auth)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_using_the_site_a_day_later_renews_the_cookie_too():
    now = [0.0]
    with running(SessionStore(clock=lambda: now[0])) as base:
        _, headers, _ = call(f"{base}/api/signin", {"email": DREW})
        token = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        assert "Set-Cookie" not in call(f"{base}/api/me", token=token)[1]  # same day: left alone
        now[0] = DAY + 5
        status, headers, _ = call(f"{base}/api/me", token=token)
        assert status == 200 and headers["Set-Cookie"].startswith(f"collectivize_session={token};")
        assert f"Max-Age={REMEMBER_SECONDS}" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
        assert "Set-Cookie" not in call(f"{base}/api/me", token=token)[1]  # once a day
        now[0] = DAY + 5 + REMEMBER_SECONDS - 60
        assert call(f"{base}/discover", token=token)[0] == 200  # 30 days after the *last* visit
        now[0] += DAY * 2 + REMEMBER_SECONDS
        assert call(f"{base}/api/me", token=token)[0] == 401


def test_signing_out_is_not_undone_by_a_renewal():
    now = [0.0]
    with running(SessionStore(clock=lambda: now[0])) as base:
        _, headers, _ = call(f"{base}/api/signin", {"email": DREW})
        token = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        now[0] = DAY + 5
        status, headers, _ = call(f"{base}/api/signout", {}, token=token)
        assert status == 200 and "Max-Age=0" in headers["Set-Cookie"]  # cleared, not renewed
        assert call(f"{base}/api/me", token=token)[0] == 401


def test_an_admin_cookie_is_never_renewed():
    now = [0.0]
    auth = AdminAuth((AdminCredential("boss", PAT, hash_password("correct horse battery")),))
    with running(SessionStore(clock=lambda: now[0]), auth) as base:
        _, headers, _ = call(f"{base}/api/admin/signin", {"username": "boss", "password": "correct horse battery"})
        token = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        now[0] = ADMIN_SESSION_SECONDS - 60
        status, headers, _ = call(f"{base}/api/me", token=token)
        assert status == 200 and "Set-Cookie" not in headers
        now[0] = ADMIN_SESSION_SECONDS + 1
        assert call(f"{base}/api/me", token=token)[0] == 401


# --- the Secure flag -------------------------------------------------------------------


def test_the_cookie_is_secure_only_when_the_site_is_reached_over_https():
    assert "Secure" not in cookie_flags({})
    assert cookie_flags({"X-Forwarded-Proto": "https"}).endswith("; Secure")
    assert cookie_flags({"X-Forwarded-Proto": "HTTPS, http"}).endswith("; Secure")
    assert "Secure" not in cookie_flags({"X-Forwarded-Proto": "http"})
    with running(SessionStore()) as base:
        plain = call(f"{base}/api/signin", {"email": DREW})[1]["Set-Cookie"]
        import urllib.request
        request = urllib.request.Request(f"{base}/api/signin", data=json.dumps({"email": DREW}).encode(),
                                         headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"})
        with urllib.request.urlopen(request) as response:
            proxied = response.headers["Set-Cookie"]
    assert "Secure" not in plain and "; Secure" in proxied and "HttpOnly" in proxied
