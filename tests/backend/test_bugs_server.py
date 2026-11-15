"""Bug hunt for the demo server (src/backend/server.py). Every test asserts the
CORRECT behaviour and is marked xfail(strict=True) because the current code
gets it wrong. No real API calls (the conftest fixture blocks the client)."""

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, resolve_static, serve

from .conftest import NOW


@pytest.fixture(scope="module")
def seeded():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    return graph, result


def api_for(graph):
    @contextmanager
    def factory():
        yield graph
    return DiscoverApi(factory, clock=lambda: NOW)


@pytest.fixture(scope="module")
def server(seeded):
    srv = serve(api_for(seeded[0]), port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def fetch(url, data=None, headers=None, method=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


# --- request parsing ------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: str.isdigit() accepts '²', int() then raises -> 500")
def test_unicode_digit_page_is_a_400_not_a_500(server):
    # "²" (superscript two) passes str.isdigit() but int("²") raises ValueError,
    # which _api turns into a 500 "something went wrong".
    status, body = fetch(f"{server}/api/discover?page=%C2%B2")
    assert status == 400 and "page" in json.loads(body)["error"]


@pytest.mark.xfail(strict=True, reason="BUG: page with >4300 digits hits int() digit limit -> 500")
def test_huge_page_number_is_a_400_not_a_500(server):
    status, body = fetch(f"{server}/api/discover?page={'9' * 5000}")
    assert status == 400 and "page" in json.loads(body)["error"]


@pytest.mark.xfail(strict=True, reason="BUG: lone surrogate in query breaks UTF-8 encode of reply -> 500")
def test_lone_surrogate_in_search_query_is_a_400_not_a_500(server):
    # Valid JSON: "\ud800" decodes to a lone surrogate. The search succeeds and
    # echoes the text (goal), then json.dumps(ensure_ascii=False).encode()
    # raises UnicodeEncodeError -> 500. The broken answer is also cached.
    data = b'{"query": "robots \\ud800"}'
    status, body = fetch(f"{server}/api/search", data, {"Content-Type": "application/json"})
    assert status == 400, (status, body)


@pytest.mark.xfail(strict=True, reason="BUG: empty POST body answered 413 'request too large'")
def test_empty_search_body_is_a_400_not_413(server):
    status, body = fetch(f"{server}/api/search", b"", {"Content-Type": "application/json"},
                         method="POST")
    assert status == 400, (status, json.loads(body))


# --- static files (Windows) ---------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: backslash URL path becomes a UNC path that is resolved (SMB)")
def test_static_never_resolves_a_unc_path(monkeypatch, tmp_path):
    # GET /%5C%5Cattacker%5Cshare%5Cx -> unquote -> "\\\\attacker\\share\\x";
    # lstrip("/") leaves the backslashes, root / that is a UNC path, and
    # .resolve() on Windows opens it (outbound SMB, NTLM credential leak)
    # BEFORE the is_relative_to check. Any web page can trigger this with
    # <img src="http://127.0.0.1:8000/%5C%5Cattacker%5Cshare%5Cx">.
    (tmp_path / "src/frontend").mkdir(parents=True)
    touched = []
    real_resolve = Path.resolve

    def spy(self, strict=False):
        touched.append(str(self))
        if str(self).startswith("\\\\"):
            return self  # don't actually hit the network in the test
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", spy)
    assert resolve_static("/%5C%5C127.0.0.1%5Cshare%5Cx", tmp_path) is None
    assert not any(p.startswith("\\\\") or p.startswith("//") for p in touched), touched


# --- privacy -----------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="BUG: personas 'friends' count ignores hide_connections")
def test_personas_do_not_reveal_hidden_connection_counts():
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)
    api = api_for(graph)
    # someone who has connections, then hides them
    target = next(p for p in api.personas()["personas"] if p["friends"] > 0)
    graph.connections.set_hide_connections(target["id"], target["id"], True)
    again = next(p for p in api.personas()["personas"] if p["id"] == target["id"])
    assert again["friends"] in (0, None), again

