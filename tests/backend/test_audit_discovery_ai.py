"""Audit of discovery, ranking, AI search and the search agent's tools.

Every test outside the "coverage" section asserts the CORRECT behaviour and
fails against the current code because of a real bug. Fake interpreters and
clients only: no test reaches the Anthropic API."""

import json
import math
from dataclasses import replace

import pytest

from backend.graph import CollectivizeGraph
from backend.graph.discovery import DiscoveryQuery, RequestMatch
from backend.graph.preferences import TasteProfile
from backend.graph.ranking import EventSignals, estimate
from backend.graph.search_tools import SearchSession, SearchTools
from backend.objects import (
    DomainRuleError,
    MemberOf,
    OrgKind,
    RecommendationUse,
    RsvpStatus,
    Tag,
    Visibility,
)

from .conftest import NOW, make_admin, make_event, make_org, make_person, staff_provenance
from .test_ai_search import FakeInterpreter


def publish(world, title, days=3, **extra):
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def make_tag(world, slug):
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


def tools_for(world, viewer):
    session = SearchSession(viewer_id=viewer.id, now=NOW, origin=None)
    g = world.graph
    return SearchTools(g.store, g.discovery, g.preferences, g.activity, g.privacy, session)


def with_fake(world):
    fake = FakeInterpreter()
    return CollectivizeGraph(world.graph.store, search_interpreter=fake), fake


def private_co_host(world, event):
    """A PRIVATE org (admin: Dave) that accepted co-hosting `event`."""
    secret = make_org(world.graph, "Secret Society", OrgKind.CLUB)
    dave = make_person(world.graph, "Dave")
    make_admin(world.graph, dave, secret)
    secret = world.graph.store.save_node(
        replace(world.graph.store.node(secret.id, type(secret)), visibility=Visibility.PRIVATE)
    )
    edge = world.graph.events.invite_co_host(event.id, secret.id, world.alice.id, NOW)
    world.graph.events.accept_co_host(edge.id, dave.id)
    return secret


# --- ranking math ----------------------------------------------------------------


def test_nan_friends_going_is_rejected_instead_of_giving_a_nan_likelihood():
    """EventSignals rejects a non-finite friends_going, like every other numeric signal."""
    try:
        signals = EventSignals(friends_going=math.nan)
    except DomainRuleError:
        return
    likelihood = estimate(signals).likelihood
    assert 0.0 <= likelihood <= 1.0, f"likelihood was {likelihood}"


def test_request_match_rejects_a_boolean_score():
    """RequestMatch rejects True/False as a score (as EventSignals and parse_answer do)."""
    with pytest.raises(DomainRuleError):
        RequestMatch(score=True)


def test_a_request_match_that_validated_never_crashes_discovery(world):
    """Any RequestMatch that constructs is usable by Discover without raising."""
    event = publish(world, "Trivia")
    try:
        match = RequestMatch(score=True)
    except DomainRuleError:
        return
    world.graph.discovery.search(DiscoveryQuery(), NOW, request_matches={event.id: match})


# --- privacy: what the AI is told --------------------------------------------------


def test_ai_search_does_not_tell_the_ai_about_a_private_co_host_org(world):
    """Orgs the viewer cannot see never reach the interpreter via top_orgs."""
    event = publish(world, "Open mic")
    secret = private_co_host(world, event)
    assert not world.graph.privacy.can_view(world.carol.id, secret)
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    graph, fake = with_fake(world)
    graph.ai_search.search(world.carol.id, "music", NOW)
    names = [name for name, _, _ in fake.requests[0].viewer.top_orgs]
    assert "Secret Society" not in names


def test_agent_active_orgs_tool_hides_a_private_co_host_org(world):
    """get_active_orgs returns only orgs the searcher may see."""
    event = publish(world, "Open mic")
    private_co_host(world, event)
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    output, is_error = tools_for(world, world.carol).run("get_active_orgs", {"limit": 10})
    assert not is_error
    assert "Secret Society" not in output


def _membership_not_for_recommendations(world):
    robotics = make_tag(world, "robotics")
    world.graph.events.tag(world.chapter.id, robotics.id, world.alice.id, NOW)
    edge = world.graph.store.edge(world.alice_edge_id, MemberOf)
    world.graph.store.save_edge(edge.with_recommendation_use(RecommendationUse.NONE))


def test_ai_search_leaves_out_org_tags_from_a_membership_marked_none(world):
    """A membership with recommendation_use=NONE contributes no org tags to the AI request."""
    _membership_not_for_recommendations(world)
    publish(world, "Anything")
    graph, fake = with_fake(world)
    graph.ai_search.search(world.alice.id, "fun", NOW)
    viewer = fake.requests[0].viewer
    assert viewer.top_orgs == ()  # activity already honours the flag
    assert viewer.org_tags == ()


def test_agent_overview_leaves_out_org_topics_from_a_membership_marked_none(world):
    """get_person_overview's org_topics skips memberships with recommendation_use=NONE."""
    _membership_not_for_recommendations(world)
    output, _ = tools_for(world, world.alice).run("get_person_overview", {})
    assert json.loads(output)["org_topics"] == []


# --- determinism ---------------------------------------------------------------------


def test_overview_orders_equally_strong_categories_deterministically(world, monkeypatch):
    """Tied learned categories come out in the same order regardless of dict insertion order."""
    a, b = make_tag(world, "alpha"), make_tag(world, "beta")
    outputs = []
    for learned in ({a.id: 1.0, b.id: 1.0}, {b.id: 1.0, a.id: 1.0}):
        profile = TasteProfile(learned=learned)
        monkeypatch.setattr(world.graph.preferences, "profile", lambda *_a, p=profile: p)
        output, _ = tools_for(world, world.carol).run("get_person_overview", {})
        outputs.append(list(json.loads(output)["goes_to_categories"]))
    assert outputs[0] == outputs[1]


# --- coverage (passing: rules not tested elsewhere) ----------------------------------


def test_agent_tools_never_list_a_private_org_by_name_search(world):
    """search_events with org_name of a private co-host org is treated as unknown."""
    event = publish(world, "Open mic")
    private_co_host(world, event)
    args = {"keywords": None, "tags": [], "org_name": "Secret", "within_days": None,
            "max_distance_km": None, "limit": 5}
    output, is_error = tools_for(world, world.carol).run("search_events", args)
    assert is_error and "no organization" in output


def test_discover_still_lists_a_public_event_co_hosted_by_a_private_org(world):
    """The primary host decides listing visibility; a hidden co-host does not hide the event."""
    event = publish(world, "Open mic")
    private_co_host(world, event)
    page = world.graph.discovery.search(DiscoveryQuery(viewer_id=world.carol.id), NOW)
    assert [i.event.id for i in page.items] == [event.id]
