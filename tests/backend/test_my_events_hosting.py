"""My Events with hosting, and its search (backend/app_api.py,
graph/unified_search.py): events you host are listed with `hosting`,
Discover results say `is_hosting`, and `scope=mine` searches only the
events in your My Events with Discover's search."""

from contextlib import contextmanager
from datetime import timedelta

import pytest

from backend.demo_api import ApiError, DiscoverApi
from backend.graph.unified_search import SearchFilters, UnifiedSearchService
from backend.objects import Event, Provenance, ProvenanceMethod

from .conftest import NOW
from .test_app_api import make_app


@pytest.fixture
def world():
    return make_app()


def _event(graph, title, host, by, *, days=2, publish=True, description=""):
    """An event `by` creates for `host` (an org they run, or themselves)."""
    start = NOW + timedelta(days=days)
    method = ProvenanceMethod.ORG_ADMIN if host != by else ProvenanceMethod.SELF_CLAIM
    event = graph.events.create(Event(
        title=title, description=description, starts_at=start, ends_at=start + timedelta(hours=1),
        is_demo=True, provenance=Provenance(created_by=by, method=method),
    ), host, by)
    if publish:
        graph.events.publish(event.id, by, NOW)
    return event


def _rsvp_api(graph):
    @contextmanager
    def factory():
        yield graph

    return DiscoverApi(factory, clock=lambda: NOW)


def _mine(app, viewer, part="upcoming"):
    return {e["id"]: e for e in app.my_events(viewer)[part]}


# --- My Events lists what you host ------------------------------------------------


def test_an_org_admin_sees_their_orgs_event_as_hosting(world):
    app, graph, r = world
    casey = r.people["casey"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    entry = _mine(app, casey)[event.id]
    assert entry["hosting"] is True and entry["rsvp"] is None and entry["saved"] is False


def test_a_student_hosting_their_own_event_sees_it(world):
    app, graph, r = world
    drew = r.people["drew"]
    event = _event(graph, "Study group", drew, drew)
    assert _mine(app, drew)[event.id]["hosting"] is True


def test_someone_who_does_not_host_never_gets_hosting(world):
    app, graph, r = world
    casey, drew = r.people["casey"], r.people["drew"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    assert event.id not in _mine(app, drew)
    _rsvp_api(graph).rsvp({"viewer": drew, "event_id": event.id, "status": "going"})
    entry = _mine(app, drew)[event.id]
    assert entry["hosting"] is False and entry["rsvp"] == "going"


def test_every_entry_has_a_hosting_flag(world):
    app, _, r = world
    for viewer in r.people.values():
        for part in ("upcoming", "past"):
            assert all(isinstance(e["hosting"], bool) for e in app.my_events(viewer)[part])


def test_hosting_keeps_going_and_saved_alongside(world):
    app, graph, r = world
    casey = r.people["casey"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    api = _rsvp_api(graph)
    api.rsvp({"viewer": casey, "event_id": event.id, "status": "going"})
    api.star({"viewer": casey, "event_id": event.id, "starred": True})
    entry = _mine(app, casey)[event.id]
    assert (entry["hosting"], entry["rsvp"], entry["saved"]) == (True, "going", True)


def test_a_hosted_event_is_listed_once(world):
    app, graph, r = world
    casey = r.people["casey"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    _rsvp_api(graph).star({"viewer": casey, "event_id": event.id, "starred": True})
    ids = [e["id"] for e in app.my_events(casey)["upcoming"]]
    assert ids.count(event.id) == 1


def test_an_unpublished_draft_is_not_listed(world):
    app, graph, r = world
    casey = r.people["casey"]
    draft = _event(graph, "Not ready yet", r.orgs["robotics"], casey, publish=False)
    assert draft.id not in _mine(app, casey)


def test_a_past_hosted_event_goes_under_past(world):
    app, graph, r = world
    casey = r.people["casey"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    later = NOW + timedelta(days=5)
    app._clock = lambda: later
    assert event.id in _mine(app, casey, "past")
    assert event.id not in _mine(app, casey, "upcoming")


def test_a_cancelled_hosted_event_says_so(world):
    app, graph, r = world
    casey = r.people["casey"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    graph.events.cancel(event.id, casey)
    assert _mine(app, casey)[event.id]["is_cancelled"] is True


def test_upcoming_stays_soonest_first_with_hosted_events(world):
    app, graph, r = world
    casey = r.people["casey"]
    _event(graph, "Later one", r.orgs["robotics"], casey, days=9)
    _event(graph, "Sooner one", r.orgs["robotics"], casey, days=1)
    starts = [e["starts_at"] for e in app.my_events(casey)["upcoming"]]
    assert starts == sorted(starts)


# --- Discover says which events you host -----------------------------------------------


def _find(app, viewer, **params):
    return app.find(viewer, {k: str(v) for k, v in params.items()})


def test_discover_marks_hosted_events_for_the_host_only(world):
    app, graph, r = world
    casey, drew = r.people["casey"], r.people["drew"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    for viewer, expected in ((casey, True), (drew, False)):
        items = {i["id"]: i for i in _find(app, viewer, q="soldering")["items"]}
        assert items[event.id]["is_hosting"] is expected


def test_every_discover_event_has_is_hosting_and_people_and_orgs_do_not(world):
    app, _, r = world
    items = _find(app, r.people["drew"], q="club")["items"] + _find(app, r.people["drew"])["items"]
    for item in items:
        if item["type"] == "event":
            assert isinstance(item["is_hosting"], bool)
        else:
            assert "is_hosting" not in item


# --- My Events search (scope=mine) ----------------------------------------------------


def test_scope_mine_finds_only_my_events(world):
    app, graph, r = world
    drew = r.people["drew"]
    mine = set(_mine(app, drew))
    data = _find(app, drew, scope="mine")
    found = {i["id"] for i in data["items"]}
    assert found and found <= mine
    assert all(i["type"] == "event" for i in data["items"])
    everything = _find(app, drew)
    assert {i["id"] for i in everything["items"]} - mine  # Discover itself is wider


def test_scope_mine_covers_every_upcoming_plan_across_pages(world):
    app, _, r = world
    drew = r.people["drew"]
    first = _find(app, drew, scope="mine")
    found = set()
    for page in range(1, first["pages"] + 1):
        found |= {i["id"] for i in _find(app, drew, scope="mine", page=page)["items"]}
    upcoming = {e["id"] for e in app.my_events(drew)["upcoming"] if not e["is_cancelled"]}
    assert upcoming <= found


def test_scope_mine_uses_keywords_like_discover(world):
    app, graph, r = world
    casey = r.people["casey"]
    wanted = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    other = _event(graph, "Board game night", r.orgs["robotics"], casey)
    found = {i["id"] for i in _find(app, casey, scope="mine", q="soldering")["items"]}
    assert wanted.id in found and other.id not in found


def test_scope_mine_forgives_a_typo_like_discover(world):
    app, graph, r = world
    casey = r.people["casey"]
    wanted = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    found = {i["id"] for i in _find(app, casey, scope="mine", q="solderinf")["items"]}
    assert wanted.id in found


def test_scope_mine_leaves_out_matching_events_that_are_not_mine(world):
    app, graph, r = world
    casey, drew = r.people["casey"], r.people["drew"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    assert event.id in {i["id"] for i in _find(app, drew, q="soldering")["items"]}
    assert event.id not in {i["id"] for i in _find(app, drew, scope="mine", q="soldering")["items"]}


def test_scope_mine_never_returns_people_or_orgs(world):
    app, _, r = world
    drew = r.people["drew"]
    for params in ({"q": "alice"}, {"q": "robotics"}, {"types": "people"}, {"types": "orgs,people"},
                   {"q": "people who like chess"}):
        items = _find(app, drew, scope="mine", **params)["items"]
        assert all(i["type"] == "event" for i in items), params


def test_scope_mine_with_no_plans_finds_nothing(world):
    app, graph, r = world
    viewer = next(p for p in r.people.values() if not app.my_events(p)["upcoming"]
                  and not app.my_events(p)["past"]) if any(
        not app.my_events(p)["upcoming"] and not app.my_events(p)["past"] for p in r.people.values()
    ) else None
    if viewer is None:
        # Everyone in the seed has plans: clear one person's.
        drew = r.people["drew"]
        api = _rsvp_api(graph)
        for e in app.my_events(drew)["upcoming"] + app.my_events(drew)["past"]:
            api.rsvp({"viewer": drew, "event_id": e["id"], "status": None})
            api.star({"viewer": drew, "event_id": e["id"], "starred": False})
        viewer = drew
        assert not any(e["hosting"] for e in app.my_events(drew)["upcoming"])
    for params in ({}, {"q": "night"}, {"q": "show me something fun"}):
        data = _find(app, viewer, scope="mine", **params)
        assert data["items"] == [] and data["total"] == 0, params


def test_scope_mine_follows_plans_as_they_change(world):
    app, graph, r = world
    casey, drew = r.people["casey"], r.people["drew"]
    event = _event(graph, "Soldering basics", r.orgs["robotics"], casey)
    api = _rsvp_api(graph)
    search = lambda: {i["id"] for i in _find(app, drew, scope="mine", q="soldering")["items"]}  # noqa: E731
    assert event.id not in search()
    api.star({"viewer": drew, "event_id": event.id, "starred": True})
    assert event.id in search()
    api.star({"viewer": drew, "event_id": event.id, "starred": False})
    api.rsvp({"viewer": drew, "event_id": event.id, "status": "maybe"})
    assert event.id in search()
    api.rsvp({"viewer": drew, "event_id": event.id, "status": None})
    assert event.id not in search()


def test_scope_mine_respects_a_date_filter(world):
    app, graph, r = world
    casey = r.people["casey"]
    soon = _event(graph, "Soldering basics", r.orgs["robotics"], casey, days=2)
    later = _event(graph, "Soldering advanced", r.orgs["robotics"], casey, days=6)
    day = (NOW + timedelta(days=2)).date().isoformat()
    found = {i["id"] for i in _find(app, casey, scope="mine", q="soldering", date=day)["items"]}
    assert soon.id in found and later.id not in found


@pytest.mark.parametrize("scope", ["all", "MINE", "mine ", "everyone", "0"])
def test_a_bad_scope_is_refused(world, scope):
    app, _, r = world
    with pytest.raises(ApiError):
        app.find(r.people["drew"], {"scope": scope})


def test_no_scope_and_an_empty_scope_are_plain_discover(world):
    app, _, r = world
    drew = r.people["drew"]
    plain = _find(app, drew)
    assert app.find(drew, {"scope": ""})["items"] == plain["items"]


# --- the search filter itself -------------------------------------------------------


def _service(graph, app):
    return UnifiedSearchService(graph.store, graph.discovery, graph.visibility, graph.privacy, app._reader)


def test_an_empty_event_id_set_finds_nothing_and_none_is_no_limit(world):
    app, graph, r = world
    drew = r.people["drew"]
    service = _service(graph, app)
    assert service.search(drew, "", SearchFilters(event_ids=frozenset()), NOW).total == 0
    unlimited = service.search(drew, "", SearchFilters(event_ids=None), NOW)
    assert unlimited.total == service.search(drew, "", SearchFilters(), NOW).total > 0


def test_event_ids_limit_results_to_exactly_those_events(world):
    app, graph, r = world
    drew = r.people["drew"]
    service = _service(graph, app)
    every = [x.id for x in service.search(drew, "", SearchFilters(), NOW, per_page=500).results]
    pick = frozenset(every[:2])
    page = service.search(drew, "", SearchFilters(event_ids=pick), NOW)
    assert {x.id for x in page.results} == pick
    assert page.applied.event_ids == pick  # carried through to what was applied


def test_unknown_event_ids_are_ignored(world):
    app, graph, r = world
    service = _service(graph, app)
    page = service.search(r.people["drew"], "", SearchFilters(event_ids=frozenset({"no-such-event"})), NOW)
    assert page.total == 0
