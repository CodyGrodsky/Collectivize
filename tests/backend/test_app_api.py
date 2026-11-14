"""The v1.1 app API (backend/app_api.py) without HTTP: connections and the
network graph (4.2), profiles (4.3), My Events (4.6) and invites (4.11).
The viewer is passed in, as the server does from the sign-in session."""

from contextlib import contextmanager

import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data

from .conftest import NOW


def make_app():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    return AppApi(factory, clock=lambda: NOW), graph, result


@pytest.fixture
def fresh():
    """A world each test may change."""
    return make_app()


@pytest.fixture(scope="module")
def shared():
    """A world tests only read."""
    return make_app()


def ids(nodes):
    return {n["id"] for n in nodes}


# --- 4.2 connections and the network graph ---------------------------------------


def test_alice_network_shows_bob_not_carol(shared):
    app, _, r = shared
    p = r.people
    mine = app.network(p["alice"], {})
    assert mine["visible"] and mine["is_own"]
    assert mine["center"]["id"] == p["alice"] and mine["center"]["is_center"]
    assert p["bob"] in ids(mine["people"]) and p["carol"] not in ids(mine["people"])
    assert r.orgs["cooking"] in ids(mine["orgs"])
    assert all(link[0] == p["alice"] for link in mine["links"])


def test_alice_sees_bobs_network_with_carol_and_his_orgs(shared):
    app, _, r = shared
    p = r.people
    bobs = app.network(p["alice"], {"id": p["bob"]})
    assert bobs["visible"] and not bobs["is_own"]
    people = {n["id"]: n for n in bobs["people"]}
    assert people[p["carol"]]["connection"] == "none" and not people[p["carol"]]["viewer_connected"]
    assert people[p["alice"]]["is_viewer"]
    assert r.orgs["surf"] in ids(bobs["orgs"])


def test_carols_network_is_hidden_from_alice(shared):
    app, _, r = shared
    p = r.people
    assert app.network(p["alice"], {"id": p["carol"]}) == {"visible": False, "id": p["carol"]}


def test_request_accept_makes_carol_first_degree(fresh):
    app, _, r = fresh
    alice, carol = r.people["alice"], r.people["carol"]
    assert app.connect(alice, {"person_id": carol})["connection"] == "pending_out"
    waiting = app.requests(carol)["incoming"]
    assert [c["id"] for c in waiting] == [alice]
    assert waiting[0]["card"] == "full" and waiting[0]["top3"]["title"] == "study spots"
    answer = app.connection(carol, {"person_id": alice, "action": "accept"})
    assert answer["connection"] == "connected"
    assert "will be able to see that you're connected to Alice" in answer["notice"]
    assert carol in ids(app.network(alice, {})["people"])


def test_decline_cancel_and_remove_leave_no_connection(fresh):
    app, graph, r = fresh
    alice, carol = r.people["alice"], r.people["carol"]
    app.connect(alice, {"person_id": carol})
    app.connection(carol, {"person_id": alice, "action": "decline"})
    assert graph.connections.between(alice, carol) is None
    app.connect(alice, {"person_id": carol})
    app.connection(alice, {"person_id": carol, "action": "cancel"})
    assert graph.connections.between(alice, carol) is None
    app.connection(alice, {"person_id": r.people["bob"], "action": "remove"})
    assert not graph.connections.are_connected(alice, r.people["bob"])


def test_asking_back_accepts_and_bad_actions_are_refused(fresh):
    app, _, r = fresh
    alice, carol = r.people["alice"], r.people["carol"]
    app.connect(alice, {"person_id": carol})
    with pytest.raises(ApiError):
        app.connection(alice, {"person_id": carol, "action": "accept"})  # only Carol can
    assert app.connect(carol, {"person_id": alice})["connection"] == "connected"
    with pytest.raises(ApiError):
        app.connection(alice, {"person_id": carol, "action": "explode"})


def test_hub_network_is_capped_with_show_more(shared):
    app, _, r = shared
    rowan = r.people["rowan"]
    first = app.network(rowan, {})
    assert first["people_total"] > 100 and len(first["people"]) == 25
    more = app.network(rowan, {"people": "50"})
    assert len(more["people"]) == 50
    with pytest.raises(ApiError):
        app.network(rowan, {"people": "lots"})
