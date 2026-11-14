"""The v1.1 app API (backend/app_api.py) without HTTP: connections (4.2),
My Events (4.6), the personal calendar (4.7) and invites (4.11).
The viewer is passed in, as the server does from the sign-in session."""

from contextlib import contextmanager

import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.objects import DomainRuleError
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


# --- 4.2 connections -----------------------------------------------------------


def test_request_accept_makes_carol_first_degree(fresh):
    app, graph, r = fresh
    alice, carol = r.people["alice"], r.people["carol"]
    assert app.connect(alice, {"person_id": carol})["connection"] == "pending_out"
    waiting = app.requests(carol)["incoming"]
    assert [c["id"] for c in waiting] == [alice]
    assert waiting[0]["top3"]["title"] == "Top 3 study spots" and waiting[0]["bio"]
    answer = app.connection(carol, {"person_id": alice, "action": "accept"})
    assert answer["connection"] == "connected"
    assert "will be able to see that you're connected to Alice" in answer["notice"]
    assert carol in {p.id for p in graph.network.node_map(alice).people}


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


# --- 4.6 RSVP, Saved and My Events -------------------------------------------------


def _calendar_ids(graph, viewer, event):
    from datetime import timedelta

    cal = graph.my_calendar.for_viewer(viewer, event.starts_at - timedelta(days=1),
                                       event.starts_at + timedelta(days=1))
    return {e.event.id for e in cal.mine}


def test_going_and_saved_show_in_my_events_and_the_calendar_and_undo_everywhere(fresh):
    from backend.demo_api import DiscoverApi

    app, graph, r = fresh
    alice, event_id = r.people["alice"], r.events["food_crawl"]
    event = graph.store.any_node(event_id)

    @contextmanager
    def factory():
        yield graph

    rsvp_api = DiscoverApi(factory, clock=lambda: NOW)
    body = {"viewer": alice, "event_id": event_id}

    rsvp_api.rsvp({**body, "status": "going"})
    mine = app.my_events(alice)["upcoming"]
    assert [(e["id"], e["rsvp"]) for e in mine if e["id"] == event_id] == [(event_id, "going")]
    assert event_id in _calendar_ids(graph, alice, event)

    rsvp_api.star({**body, "starred": True})
    entry = next(e for e in app.my_events(alice)["upcoming"] if e["id"] == event_id)
    assert entry["saved"] and entry["rsvp"] == "going"

    rsvp_api.rsvp({**body, "status": None})
    rsvp_api.star({**body, "starred": False})
    assert event_id not in {e["id"] for e in app.my_events(alice)["upcoming"]}
    assert event_id not in _calendar_ids(graph, alice, event)


def test_a_full_event_closes_going(fresh):
    from dataclasses import replace

    from backend.demo_api import DiscoverApi

    app, graph, r = fresh
    event_id = r.events["food_crawl"]

    @contextmanager
    def factory():
        yield graph

    rsvp_api = DiscoverApi(factory, clock=lambda: NOW)
    going = graph.rsvps._going(event_id)
    graph.store.save_node(replace(graph.store.any_node(event_id), capacity=going + 1))
    state = rsvp_api.rsvp({"viewer": r.people["alice"], "event_id": event_id, "status": "going"})
    assert state["is_full"]
    with pytest.raises(DomainRuleError):
        rsvp_api.rsvp({"viewer": r.people["carol"], "event_id": event_id, "status": "going"})


# --- 4.7 personal calendar --------------------------------------------------------


def test_calendar_shows_only_my_plans_and_my_orgs_events(fresh):
    from datetime import timedelta

    from backend.objects import Event, Provenance, ProvenanceMethod

    app, graph, r = fresh
    drew, casey = r.people["drew"], r.people["casey"]
    # A Robotics Club event: Drew isn't in the club and has no plans for it.
    start = NOW + timedelta(days=2)
    event = graph.events.create(Event(
        title="Soldering basics", starts_at=start, ends_at=start + timedelta(hours=1),
        is_demo=True, provenance=Provenance(created_by=casey, method=ProvenanceMethod.ORG_ADMIN),
    ), r.orgs["robotics"], casey)
    graph.events.publish(event.id, casey, NOW)
    window = {"start": (NOW - timedelta(days=3)).isoformat(),
              "end": (NOW + timedelta(days=40)).isoformat()}

    data = app.my_calendar(drew, window)
    personal = {"going", "maybe", "starred", "hosting", "my_org"}
    assert data["entries"] and all(set(e["relations"]) & personal for e in data["entries"])
    assert event.id not in {e["id"] for e in data["entries"]}
    assert event.id in {e["id"] for e in app.my_calendar(casey, window)["entries"]}  # Casey's org
    with pytest.raises(ApiError):
        app.my_calendar(drew, {**window, "start": "yesterday"})


# --- 4.11 invite preview card ------------------------------------------------------


def test_invite_link_shows_the_inviters_card_to_anyone(fresh):
    app, _, r = fresh
    link = app.create_invite(r.people["alice"])
    assert link["path"] == f"/invite/{link['code']}"
    preview = app.invite_preview({"code": link["code"]})["inviter"]
    assert preview["id"] == r.people["alice"] and preview["colors"]["outline"].startswith("#")
    assert preview["top3"]["title"] == "Top 3 study spots"
    assert "orgs" not in preview and "events" not in preview
    with pytest.raises(ApiError):
        app.invite_preview({"code": "not-a-real-invite-code"})


def test_joining_by_invite_connects_with_the_inviter(fresh):
    app, graph, r = fresh
    code = app.create_invite(r.people["alice"])["code"]
    newcomer = app.sign_in({"email": "nia.new@northeastern.edu", "invite": code})
    assert graph.connections.are_connected(newcomer, r.people["alice"])
    with pytest.raises(ApiError):  # single use
        app.invite_preview({"code": code})


def test_an_existing_account_accepting_an_invite_connects(fresh):
    app, graph, r = fresh
    alice, carol = r.people["alice"], r.people["carol"]
    code = app.create_invite(alice)["code"]
    assert app.sign_in({"email": "carol.demo@northeastern.edu", "invite": code}) == carol
    assert graph.connections.are_connected(alice, carol)


def test_a_bad_invite_never_blocks_sign_in(fresh):
    app, graph, r = fresh
    person = app.sign_in({"email": "zed.new@northeastern.edu", "invite": "nope-nope-nope"})
    assert graph.connections.connections_of(person) == []
    with pytest.raises(ApiError):
        app.sign_in({"email": "someone@gmail.com", "invite": "x"})
