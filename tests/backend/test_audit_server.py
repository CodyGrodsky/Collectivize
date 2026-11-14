"""Audit of the demo server (src/backend/server.py): bugs found while
reviewing it, plus extra coverage. Tests under "bugs" fail on the current
code; see docs/TEST_AUDIT.md for each one."""

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.objects import MemberOf, Organization, RsvpStatus, Visibility
from backend.seed import seed_mock_data
from backend.server import ApiError, DiscoverApi, _step_json, serve
from backend.graph.search_agent import AgentStep

from .conftest import NOW


def fresh_world():
    graph = CollectivizeGraph()
    return graph, seed_mock_data(graph, Environment.TEST, NOW)


def api_for(graph):
    @contextmanager
    def factory():
        yield graph
    return DiscoverApi(factory, clock=lambda: NOW)


@pytest.fixture(scope="module")
def world():
    return fresh_world()


@pytest.fixture(scope="module")
def server(world):
    srv = serve(api_for(world[0]), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def fetch(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def post_search(server, body):
    return fetch(f"{server}/api/search", json.dumps(body).encode(),
                 {"Content-Type": "application/json"})


# --- bugs ----------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["%C2%B2", "%C2%B9"])  # "²", "¹": isdigit() but not int()
def test_unicode_superscript_page_is_a_400_not_a_500(server, page):
    """A page number like '²' is bad input (400), not a server crash (500)."""
    status, body = fetch(f"{server}/api/discover?page={page}")
    assert status == 400, json.loads(body)


def test_personas_do_not_reveal_private_memberships():
    """A membership the person made private is not shown in the persona list."""
    graph, seeded = fresh_world()
    drew = seeded.people["drew"]
    for edge in graph.store.edges(MemberOf, source_id=drew):
        if edge.is_confirmed_current:
            graph.membership.set_visibility(edge.id, drew, Visibility.PRIVATE)
    persona = next(p for p in api_for(graph).personas()["personas"] if p["id"] == drew)
    hidden = [graph.store.node(e.target_id, Organization).name.removeprefix("[Demo] ")
              for e in graph.store.edges(MemberOf, source_id=drew)]
    assert not any(name in persona["description"] for name in hidden), persona


def test_cached_search_reflects_a_new_rsvp():
    """After the viewer RSVPs, a repeated search shows their new RSVP, not a
    stale cached copy (the cache has no expiry or invalidation)."""
    graph, seeded = fresh_world()
    api = api_for(graph)
    drew = seeded.people["drew"]
    body = {"viewer": drew, "query": "puzzles", "near": False}
    first = api.search(body)
    target = next(i for i in first["items"] if i["my_rsvp"] is None and not i["is_cancelled"])
    graph.rsvps.respond(target["id"], drew, RsvpStatus.GOING, NOW)
    again = api.search(body)
    item = next(i for i in again["items"] if i["id"] == target["id"])
    assert item["my_rsvp"] == "going"


def test_step_text_survives_odd_tool_input():
    """A model-supplied step with odd input types still renders, never crashes."""
    step = AgentStep(tool="search_events", input={"tags": [1, 2], "keywords": "x"},
                     is_error=False)
    assert _step_json(step)["text"].startswith("Searched events")


# --- coverage ------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "x" * 501])
def test_rejected_search_text_is_a_400_over_http(server, query):
    """Empty or over-long search text is a clear 400, not 'something went wrong'.
    The agent raises DomainRuleError (a ValueError), which the handler maps to
    400 -- this checks the HTTP status stays correct."""
    status, body = post_search(server, {"query": query})
    assert status == 400 and json.loads(body)["error"] != "something went wrong"


@pytest.mark.parametrize("length", ["0", "-5"])
def test_empty_or_negative_content_length_is_refused(server, length):
    """A POST with no body is refused with a JSON error."""
    request = urllib.request.Request(f"{server}/api/search", data=b"",
                                     headers={"Content-Type": "application/json",
                                              "Content-Length": length})
    try:
        urllib.request.urlopen(request)
        pytest.fail("expected an error")
    except urllib.error.HTTPError as error:
        assert 400 <= error.code < 500 and "error" in json.loads(error.read())


def test_post_to_other_paths_is_404(server):
    """Only /api/search accepts POST."""
    assert fetch(f"{server}/api/discover", b"{}", {"Content-Type": "application/json"})[0] == 404


def test_page_count_matches_total(world):
    """`pages` is ceil(total / per_page) and the last page is not empty."""
    api = api_for(world[0])
    data = api.discover({})
    last = api.discover({"page": str(data["pages"])})
    assert data["pages"] == -(-data["total"] // data["per_page"])
    assert last["items"] and len(last["items"]) <= data["per_page"]


def test_search_cache_is_bounded(world):
    """The search cache never holds more than CACHE_SIZE answers."""
    from backend.server import CACHE_SIZE
    api = api_for(world[0])
    for i in range(CACHE_SIZE + 5):
        api.search({"query": f"thing {i}"})
    assert len(api._cache) == CACHE_SIZE


def test_unknown_topic_is_rejected(world):
    """An unknown topic slug is a 400, not an empty page."""
    with pytest.raises(ApiError):
        api_for(world[0]).discover({"tag": "no-such-topic"})
