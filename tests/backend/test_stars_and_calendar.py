"""Stars (private bookmarks), the personal calendar, and the API endpoints
the Discover cards and calendar page use. Includes fuzzing the calendar's
inputs so no input can break it."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.demo_api import ApiError, DiscoverApi
from backend.graph import NotFoundError
from backend.graph.my_calendar import MAX_COMMUNITY, MAX_RANGE, Relation
from backend.objects import DomainRuleError, RsvpStatus, Starred, Visibility

from .conftest import NOW, make_event, make_person

WEEK = timedelta(days=7)


def publish(world, title="Bonfire", days=3, org=None, admin=None, **extra):
    org, admin = org or world.chapter, admin or world.alice
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, org.id, admin.id)
    return world.graph.events.publish(event.id, admin.id, NOW)


def calendar(world, viewer, start=NOW - WEEK, end=NOW + 4 * WEEK):
    return world.graph.my_calendar.for_viewer(viewer.id if viewer else None, start, end)


def mine(cal):
    return {e.event.title: {r.value for r in e.relations} for e in cal.mine}


def community(cal):
    return {e.event.title: {r.value for r in e.relations} for e in cal.community}


# --- stars -------------------------------------------------------------------------


def test_star_is_private_idempotent_and_removable(world):
    event = publish(world)
    stars = world.graph.stars
    first = stars.star(event.id, world.carol.id, NOW)
    assert stars.star(event.id, world.carol.id, NOW).id == first.id
    assert first.visibility is Visibility.PRIVATE
    assert stars.starred_event_ids(world.carol.id) == {event.id}
    assert stars.unstar(event.id, world.carol.id) is True
    assert stars.unstar(event.id, world.carol.id) is False  # nothing left to remove
    assert world.graph.store.edges(Starred, include_inactive=True) == []


def test_stars_never_count_as_going_or_show_to_friends(world):
    event = publish(world)
    world.graph.connections.accept(
        world.graph.connections.request(world.alice.id, world.carol.id, NOW).id, world.carol.id)
    world.graph.stars.star(event.id, world.carol.id, NOW)
    assert world.graph.rsvps.friends_going(world.alice.id, event.id) == ()
    assert not world.graph.privacy.can_view(world.alice.id, world.graph.stars.star_of(
        event.id, world.carol.id))


def test_cannot_star_what_you_cannot_see_or_what_is_not_listed(world):
    hidden = publish(world, "Members only", visibility=Visibility.MEMBERS)
    draft = world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    archived = world.graph.events.archive(publish(world, "Old").id, world.alice.id)
    for event_id in (hidden.id, draft.id, archived.id, "missing", world.chapter.id):
        with pytest.raises(NotFoundError):
            world.graph.stars.star(event_id, world.carol.id, NOW)


def test_cannot_star_an_ended_or_archived_event_but_can_always_unstar(world):
    event = publish(world)
    world.graph.stars.star(event.id, world.carol.id, NOW)
    with pytest.raises(DomainRuleError, match="ended"):
        world.graph.stars.star(event.id, world.carol.id, event.ends_at)
    world.graph.events.archive(event.id, world.alice.id)
    assert world.graph.stars.unstar(event.id, world.carol.id) is True


def test_erasing_an_account_removes_its_stars(world):
    event = publish(world)
    world.graph.stars.star(event.id, world.carol.id, NOW)
    world.graph.privacy.erase_person(world.carol.id)
    assert world.graph.store.edges(Starred, include_inactive=True) == []
    assert world.graph.integrity.audit() == ()


# --- the personal calendar -------------------------------------------------------


def test_my_events_are_what_i_chose_and_the_rest_is_community(world):
    going, maybe, starred, other = (publish(world, t) for t in ("Going", "Maybe", "Starred", "Other"))
    hosted = publish(world, "National gala", org=world.national, admin=world.bob)
    world.graph.rsvps.respond(going.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.rsvps.respond(maybe.id, world.carol.id, RsvpStatus.INTERESTED, NOW)
    world.graph.stars.star(starred.id, world.carol.id, NOW)
    cal = calendar(world, world.carol)
    assert mine(cal) == {"Going": {"going"}, "Maybe": {"maybe"}, "Starred": {"starred"}}
    assert set(community(cal)) == {"Other", hosted.title}
    assert mine(calendar(world, world.bob)) == {"National gala": {"hosting", "my_org"}}
    assert other.id


def test_unclicking_going_takes_it_off_my_events(world):
    event = publish(world, "Trivia")
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    assert "Trivia" in mine(calendar(world, world.carol))
    world.graph.rsvps.respond(event.id, world.carol.id, None, NOW)
    cal = calendar(world, world.carol)
    assert "Trivia" not in mine(cal) and "Trivia" in community(cal)


def test_my_orgs_events_stay_in_community_but_are_marked(world):
    edge = world.graph.membership.request(world.carol.id, world.chapter.id, NOW)
    world.graph.membership.confirm(edge.id, world.alice.id)
    publish(world, "Chapter meeting")
    cal = calendar(world, world.carol)
    assert community(cal) == {"Chapter meeting": {"my_org"}} and mine(cal) == {}


def test_hosts_see_their_drafts_and_nobody_else_does(world):
    world.graph.events.create(make_event("Secret draft"), world.chapter.id, world.alice.id)
    assert "Secret draft" in mine(calendar(world, world.alice))
    for viewer in (world.carol, None):
        cal = calendar(world, viewer)
        assert "Secret draft" not in mine(cal) and "Secret draft" not in community(cal)


def test_hidden_archived_and_out_of_window_events_are_left_out(world):
    publish(world, "Members night", visibility=Visibility.MEMBERS)
    world.graph.events.archive(publish(world, "Archived").id, world.alice.id)
    publish(world, "Far future", days=90)
    titles = set(community(calendar(world, world.carol))) | set(mine(calendar(world, world.carol)))
    assert titles.isdisjoint({"Members night", "Archived", "Far future"})
    assert "Members night" in set(community(calendar(world, world.alice))) | set(
        mine(calendar(world, world.alice)))


def test_cancelled_events_stay_and_events_spanning_the_window_count(world):
    cancelled = publish(world, "Called off")
    world.graph.events.cancel(cancelled.id, world.alice.id)
    retreat = publish(world, "Retreat", days=1)
    world.graph.events.revise(retreat.id, world.alice.id, NOW,
                              ends_at=retreat.starts_at + timedelta(days=10))
    window_start = retreat.starts_at + timedelta(days=3)  # starts before the window
    cal = calendar(world, world.carol, window_start, window_start + timedelta(days=1))
    assert set(community(cal)) == {"Retreat"}
    assert any(e.event.title == "Called off" for e in calendar(world, world.carol).community)


def test_logged_out_calendar_has_no_my_events(world):
    publish(world, "Open house")
    cal = calendar(world, None)
    assert cal.mine == () and "Open house" in community(cal)


def test_community_list_is_capped_and_says_so(world):
    for i in range(MAX_COMMUNITY + 3):
        publish(world, f"E{i}", days=1 + i % 20)
    cal = calendar(world, world.carol)
    assert len(cal.community) == MAX_COMMUNITY and cal.community_truncated


@pytest.mark.parametrize(("start", "end"), [
    (NOW, NOW), (NOW + WEEK, NOW), (NOW, NOW + MAX_RANGE + timedelta(seconds=1)),
    (datetime(2026, 11, 1), datetime(2026, 11, 30)),  # no time zone
])
def test_bad_windows_are_rejected(world, start, end):
    with pytest.raises(DomainRuleError):
        world.graph.my_calendar.for_viewer(world.carol.id, start, end)


def test_unknown_viewer_is_not_found(world):
    with pytest.raises(NotFoundError):
        world.graph.my_calendar.for_viewer("nobody", NOW, NOW + WEEK)


def test_any_time_zone_gives_the_same_events(world):
    publish(world, "Late night", days=2)
    tokyo = timezone(timedelta(hours=9))
    utc = calendar(world, world.carol)
    shifted = calendar(world, world.carol, (NOW - WEEK).astimezone(tokyo),
                       (NOW + 4 * WEEK).astimezone(tokyo))
    assert [e.event.id for e in utc.community] == [e.event.id for e in shifted.community]


def test_relation_values_are_stable():
    assert {r.value for r in Relation} == {"hosting", "going", "maybe", "starred", "my_org"}


# --- the API the pages use ---------------------------------------------------------


@pytest.fixture
def api(world):
    @contextmanager
    def factory():
        yield world.graph
    return DiscoverApi(factory, clock=lambda: NOW)


def test_rsvp_endpoint_sets_changes_and_clears(world, api):
    event = publish(world)
    body = {"viewer": world.carol.id, "event_id": event.id}
    assert api.rsvp({**body, "status": "going"})["my_rsvp"] == "going"
    assert api.rsvp({**body, "status": "maybe"})["my_rsvp"] == "interested"
    cleared = api.rsvp({**body, "status": None})
    assert cleared == {"event_id": event.id, "my_rsvp": None, "starred": False,
                       "going_count": None, "is_full": False}


def test_star_endpoint_toggles(world, api):
    event = publish(world)
    body = {"viewer": world.carol.id, "event_id": event.id}
    assert api.star({**body, "starred": True})["starred"] is True
    assert api.star({**body, "starred": False})["starred"] is False


def test_discover_items_say_whether_you_starred_them(world, api):
    event = publish(world)
    api.star({"viewer": world.carol.id, "event_id": event.id, "starred": True})
    item = api.discover({"viewer": world.carol.id})["items"][0]
    assert item["starred"] is True and item["id"] == event.id


@pytest.mark.parametrize("body", [
    {"status": "going"},  # no viewer
    {"viewer": "", "status": "going"},
    {"viewer": 5, "status": "going"},
    {"viewer": "carol", "status": "yes"},
    {"viewer": "carol", "status": "interested"},  # the API takes "maybe"
    {"viewer": "carol", "status": "going", "event_id": None},
    {"viewer": "carol", "status": "going", "event_id": "x" * 65},
])
def test_bad_rsvp_requests_are_rejected(world, api, body):
    body = {k: (world.carol.id if v == "carol" else v) for k, v in body.items()}
    with pytest.raises(ApiError):
        api.rsvp(body)


def test_rsvp_rules_still_apply_through_the_api(world, api):
    full = publish(world, "Tiny", capacity=1)
    api.rsvp({"viewer": world.alice.id, "event_id": full.id, "status": "going"})
    with pytest.raises(DomainRuleError, match="full"):
        api.rsvp({"viewer": world.carol.id, "event_id": full.id, "status": "going"})
    hidden = publish(world, "Members night", visibility=Visibility.MEMBERS)
    with pytest.raises(NotFoundError):
        api.rsvp({"viewer": world.carol.id, "event_id": hidden.id, "status": "going"})


@pytest.mark.parametrize("starred", [None, "true", 1, "yes"])
def test_star_needs_a_real_boolean(world, api, starred):
    event = publish(world)
    with pytest.raises(ApiError):
        api.star({"viewer": world.carol.id, "event_id": event.id, "starred": starred})


def test_calendar_endpoint(world, api):
    event = publish(world, "Trivia")
    api.rsvp({"viewer": world.carol.id, "event_id": event.id, "status": "going"})
    data = api.calendar({"viewer": world.carol.id, "start": (NOW - WEEK).isoformat(),
                         "end": (NOW + WEEK).isoformat()})
    [entry] = data["mine"]
    assert entry["relations"] == ["going"] and entry["title"] == "Trivia"
    assert entry["host"] == "Alpha Chapter" and not entry["is_draft"]


@pytest.mark.parametrize("params", [
    {}, {"start": "2026-11-01T00:00:00+00:00"}, {"start": "nope", "end": "nope"},
    {"start": "2026-11-01T00:00:00", "end": "2026-11-30T00:00:00"},  # no time zone
    {"start": "2026-11-30T00:00:00+00:00", "end": "2026-11-01T00:00:00+00:00"},
    {"start": "2026-01-01T00:00:00+00:00", "end": "2026-12-31T00:00:00+00:00"},
    {"start": "x" * 41, "end": "2026-11-30T00:00:00+00:00"},
    {"viewer": "nobody", "start": "2026-11-01T00:00:00+00:00", "end": "2026-11-30T00:00:00+00:00"},
])
def test_bad_calendar_requests_are_rejected_cleanly(api, params):
    with pytest.raises((ApiError, DomainRuleError)):
        api.calendar(params)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.dictionaries(st.sampled_from(["viewer", "start", "end"]), st.text(max_size=50)))
def test_calendar_never_crashes_on_any_input(api, params):
    try:
        data = api.calendar(params)
    except (ApiError, DomainRuleError, NotFoundError):
        return
    assert set(data) >= {"mine", "community"}


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.dictionaries(
    st.sampled_from(["viewer", "event_id", "status", "starred", "near"]),
    st.none() | st.booleans() | st.integers() | st.text(max_size=20) | st.lists(st.integers()),
))
def test_rsvp_and_star_never_crash_on_any_input(api, body):
    for call in (api.rsvp, api.star):
        try:
            call(body)
        except (ApiError, DomainRuleError, NotFoundError):
            pass


def test_cached_search_shows_fresh_rsvp_state(world, api):
    event = publish(world, "Robot night", description="robots")
    body = {"viewer": world.carol.id, "query": "robots"}
    first = api.search(body)
    assert first["items"][0]["my_rsvp"] is None
    api.rsvp({"viewer": world.carol.id, "event_id": event.id, "status": "going"})
    again = api.search(body)
    assert again["cached"] and again["items"][0]["my_rsvp"] == "going"


def test_unknown_viewers_and_people_who_left_cannot_rsvp(world, api):
    event = publish(world)
    ghost = make_person(world.graph, "Ghost")
    world.graph.privacy.erase_person(ghost.id)
    with pytest.raises(ApiError):
        api.rsvp({"viewer": ghost.id, "event_id": event.id, "status": "going"})
