"""The v1.1 app API (backend/app_api.py) without HTTP: connections and the
network graph (4.2), profiles (4.3), My Events (4.6) and invites (4.11).
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


# --- 4.3 profile -----------------------------------------------------------------

JPEG = "/9j/" + "A" * 40  # base64 of bytes starting with the JPEG signature


def test_own_and_connection_profiles_show_every_section(shared):
    app, _, r = shared
    p = r.people
    mine = app.profile(p["alice"], {})
    assert mine["is_self"] and mine["card"]["card"] == "full"
    assert mine["sections"] == {"orgs": True, "events": True, "network": True}
    assert "palette" in mine and "upcoming" in mine
    bobs = app.profile(p["alice"], {"id": p["bob"]})
    assert not bobs["is_self"] and bobs["sections"]["network"]
    assert [o["id"] for o in bobs["orgs"]] == [r.orgs["surf"]]
    assert set(bobs["events"]) == {"going", "attended", "hosted"}
    assert "email" not in bobs and "upcoming" not in bobs


def test_non_connection_sees_only_the_partial_card(shared):
    app, _, r = shared
    p = r.people
    carol = app.profile(p["alice"], {"id": p["carol"]})
    assert carol["card"]["card"] == "partial" and carol["card"]["connection"] == "none"
    assert carol["card"]["top3"]["title"] == "hobbies" and carol["card"]["bio"]
    assert carol["sections"] == {"orgs": False, "events": False, "network": False}
    assert "orgs" not in carol and "events" not in carol


def test_events_only_appear_below_the_card_for_self_and_connections(shared):
    app, _, r = shared
    p = r.people
    drew_as_friend = app.profile(p["alice"], {"id": p["drew"]})  # Alice and Drew are connected
    assert drew_as_friend["events"]["hosted"], "Drew hosts the study jam"
    assert "events" not in drew_as_friend["card"]
    stranger = app.profile(p["carol"], {"id": p["drew"]})
    assert "events" not in stranger


def test_editing_your_profile(fresh):
    app, _, r = fresh
    alice = r.people["alice"]
    updated = app.update_profile(alice, {
        "bio": "  New   bio ", "top3": {"title": "hobbies", "items": ["a", "b", "c"]},
        "theme": {"outline": "coral", "background": "sky"}, "name": "Alice A.",
    })
    card = updated["card"]
    assert card["bio"] == "New bio" and card["name"] == "Alice A."
    assert card["top3"] == {"title": "hobbies", "items": ["a", "b", "c"]}
    assert card["theme"]["outline"] == "coral" and card["theme"]["background_hex"].startswith("#")
    assert app.update_profile(alice, {"top3": None})["card"]["top3"] is None


@pytest.mark.parametrize("body", [
    {"top3": {"title": "x", "items": ["a", "b"]}},
    {"top3": {"title": "x" * 31, "items": ["a", "b", "c"]}},
    {"theme": {"outline": "#ff0000", "background": "paper"}},
    {"bio": "x" * 161},
    {"name": "   "},
    {"email": "steal@northeastern.edu"},
])
def test_bad_profile_edits_are_refused(fresh, body):
    app, _, r = fresh
    with pytest.raises((ApiError, DomainRuleError)):  # both become a 400
        app.update_profile(r.people["alice"], body)


def test_photos_upload_and_follow_card_visibility(fresh):
    app, _, r = fresh
    p = r.people
    app.upload_photo(p["alice"], {"slot": 0, "content_type": "image/jpeg", "data_b64": JPEG})
    app.upload_photo(p["alice"], {"slot": 1, "content_type": "image/jpeg", "data_b64": JPEG})
    card = app.profile(p["alice"], {})["card"]
    assert card["has_photo"] and card["pictures"] == [1]
    assert app.photo(p["carol"], {"id": p["alice"], "slot": "0"})[0] == "image/jpeg"  # on every card
    with pytest.raises(ApiError):
        app.photo(p["carol"], {"id": p["alice"], "slot": "1"})  # pictures: full card only
    assert app.photo(p["bob"], {"id": p["alice"], "slot": "1"})[0] == "image/jpeg"
    app.upload_photo(p["alice"], {"slot": 0, "remove": True})
    assert not app.profile(p["alice"], {})["card"]["has_photo"]


def test_fake_images_are_refused(fresh):
    app, _, r = fresh
    with pytest.raises(DomainRuleError):
        app.upload_photo(r.people["alice"], {"slot": 0, "content_type": "image/png", "data_b64": JPEG})
    with pytest.raises(ApiError):
        app.upload_photo(r.people["alice"], {"slot": 9, "content_type": "image/jpeg", "data_b64": JPEG})


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
