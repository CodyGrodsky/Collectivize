"""The search agent: a scripted fake client plays Claude's side, so the
whole tool loop runs without API calls. One live test is opt-in."""

import json
import os
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.graph import CollectivizeGraph
from backend.graph.search_agent import MAX_TOOL_CALLS, MAX_TURNS
from backend.graph.search_tools import (
    MAX_PICKS,
    TOOL_DEFINITIONS,
    SearchSession,
    SearchTools,
)
from backend.objects import GeoPoint, Person, RsvpStatus, Tag, Visibility

from .conftest import NOW, make_event, staff_provenance

CAMPUS = GeoPoint(42.34, -71.09)


class ScriptedClient:
    """Returns the scripted turns in order; each turn is a list of blocks or
    a (stop_reason, blocks) pair. Records every request."""

    def __init__(self, turns, error=None):
        self.turns, self.error, self.calls = list(turns), error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if self.error:
            raise self.error
        if not self.turns:
            return SimpleNamespace(stop_reason="end_turn", content=[text("done")])
        turn = self.turns.pop(0)
        stop, blocks = turn if isinstance(turn, tuple) else (None, turn)
        if stop is None:
            stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(stop_reason=stop, content=blocks)


_ids = iter(range(10_000))


def call(name, **tool_input):
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=tool_input)


def text(value):
    return SimpleNamespace(type="text", text=value)


def search(**overrides):
    base = {"keywords": None, "tags": [], "org_name": None, "within_days": None,
            "max_distance_km": None, "limit": 10}
    return call("search_events", **{**base, **overrides})


def submit(goal, *picks):
    return call("submit_picks", goal=goal,
                picks=[{"ref": r, "fit": f, "reason": why} for r, f, why in picks])


def agent_graph(world, turns, **kwargs):
    client = ScriptedClient(turns, **kwargs)
    return CollectivizeGraph(world.graph.store, agent_client=client), client


def publish(world, title, days=3, **extra):
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, world.chapter.id, world.alice.id)
    return world.graph.events.publish(event.id, world.alice.id, NOW)


def titles(page):
    return [i.event.title for i in page.items]


def tool_results(client, request_index):
    """The tool_result blocks sent back in a given request."""
    return client.calls[request_index]["messages"][-1]["content"]


# --- the happy path ------------------------------------------------------------


def test_agent_looks_up_the_person_searches_and_presents_its_picks(world):
    publish(world, "Puzzle hunt", description="Fast-paced team puzzles around campus")
    publish(world, "Pottery", description="Slow, relaxing clay work")
    graph, client = agent_graph(world, [
        [text("They want something quick and brainy."), call("get_person_overview")],
        [search(keywords="puzzles fast")],
        [submit("Quick, brain-teasing activities", ("e1", 0.95, "Fast team puzzles"))],
    ])
    result = graph.search_agent.search(world.carol.id, "fast paced puzzles", NOW)
    assert result.used_agent and result.note is None
    assert result.goal == "Quick, brain-teasing activities"
    assert [s.tool for s in result.steps] == ["get_person_overview", "search_events", "submit_picks"]
    assert titles(result.page) == ["Puzzle hunt"]
    assert "Fits what you asked for: Fast team puzzles" in result.page.items[0].reasons
    assert len(client.calls) == 3  # stops as soon as picks are submitted


def test_the_conversation_is_kept_append_only(world):
    publish(world, "Trivia")
    graph, client = agent_graph(world, [
        [call("list_tags"), call("get_active_orgs", limit=3)],
        [search()],
        [submit("trivia", ("e1", 0.8, "quiz fun"))],
    ])
    graph.search_agent.search(world.carol.id, "quiz", NOW)
    second = client.calls[1]["messages"]
    assert second[0] == {"role": "user", "content": "Search bar: quiz"}
    assert second[1]["role"] == "assistant"  # the full response content, unedited
    results = second[2]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]  # one user message
    ids = [b.id for b in second[1]["content"] if b.type == "tool_use"]
    assert [r["tool_use_id"] for r in results] == ids
    assert client.calls[2]["messages"][:3] == second  # earlier turns never change


def test_requests_use_the_right_settings(world):
    publish(world, "Anything")
    graph, client = agent_graph(world, [[search()], [submit("x", ("e1", 0.5, "ok"))]])
    graph.search_agent.search(world.carol.id, "x", NOW)
    req = client.calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"] == {"effort": "medium"}
    assert req["cache_control"] == {"type": "ephemeral"}
    assert all(t["strict"] for t in req["tools"])
    assert "tool_choice" not in req and "thinking" not in req


def test_tool_schemas_are_strict_compatible():
    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for prop in schema["properties"].values():
                check(prop)
        if schema.get("type") == "array":
            check(schema["items"])
    for tool in TOOL_DEFINITIONS:
        check(tool["input_schema"])


# --- ranking the picks ---------------------------------------------------------


def connect(world, a, b):
    world.graph.connections.accept(world.graph.connections.request(a.id, b.id, NOW).id, b.id)


def test_other_signals_order_equally_good_picks(world):
    publish(world, "Chess blitz")
    with_friend = publish(world, "Go tournament")
    connect(world, world.carol, world.alice)
    world.graph.rsvps.respond(with_friend.id, world.alice.id, RsvpStatus.GOING, NOW)
    graph, _ = agent_graph(world, [
        [search()],
        [submit("strategy games", ("e1", 0.9, "fast strategy"), ("e2", 0.9, "fast strategy"))],
    ])
    result = graph.search_agent.search(world.carol.id, "strategy games", NOW)
    assert titles(result.page) == ["Go tournament", "Chess blitz"]


def test_only_picked_events_are_presented(world):
    for days, title in enumerate(("A", "B", "C"), start=1):
        publish(world, title, days=days)  # different days: a fixed order, so e2 is "B"
    graph, _ = agent_graph(world, [[search()], [submit("g", ("e2", 0.7, "fits"))]])
    assert titles(graph.search_agent.search(world.carol.id, "q", NOW).page) == ["B"]


def test_submitting_no_picks_means_nothing_fits(world):
    publish(world, "Unrelated")
    graph, _ = agent_graph(world, [[search()], [submit("skydiving")]])
    result = graph.search_agent.search(world.carol.id, "skydiving", NOW)
    assert result.used_agent and result.page.items == ()


# --- the agent can recover from its own mistakes -------------------------------


def test_bad_tool_input_comes_back_as_an_error_it_can_fix(world):
    publish(world, "Robot race")
    world.graph.store.save_node(Tag(slug="robotics", label="Robotics", provenance=staff_provenance()))
    graph, client = agent_graph(world, [
        [search(tags=["robots"])],  # wrong slug
        [call("list_tags")],
        [search(keywords="robot")],
        [submit("robots", ("e1", 0.9, "robot racing"))],
    ])
    result = graph.search_agent.search(world.carol.id, "robots", NOW)
    first = tool_results(client, 1)[0]
    assert first["is_error"] and "list_tags" in json.loads(first["content"])["error"]
    assert result.used_agent and titles(result.page) == ["Robot race"]
    assert [s.is_error for s in result.steps] == [True, False, False, False]


def test_picking_an_event_it_never_saw_is_rejected(world):
    publish(world, "Real")
    graph, client = agent_graph(world, [
        [search()],
        [submit("x", ("e99", 1.0, "made up"))],
        [submit("x", ("e1", 0.8, "real"))],
    ])
    result = graph.search_agent.search(world.carol.id, "x", NOW)
    assert tool_results(client, 2)[0]["is_error"]
    assert titles(result.page) == ["Real"]


def test_unknown_tools_are_errors_not_crashes(world):
    publish(world, "E")
    graph, client = agent_graph(world, [
        [call("delete_everything")], [search()], [submit("x", ("e1", 0.5, "ok"))],
    ])
    result = graph.search_agent.search(world.carol.id, "x", NOW)
    assert tool_results(client, 1)[0]["is_error"] and result.used_agent


# --- falling back --------------------------------------------------------------


@pytest.mark.parametrize("turns", [
    [[text("Here are some ideas!")]],  # answers in prose, never submits
    [("refusal", [text("")])],
    [("max_tokens", [search()])],
    [[call("list_tags")]] * (MAX_TURNS + 1),  # never finishes
])
def test_runs_that_dont_finish_fall_back(world, turns):
    publish(world, "Fallback event", description="puzzles")
    graph, _ = agent_graph(world, turns)
    result = graph.search_agent.search(world.carol.id, "puzzles", NOW)
    assert not result.used_agent and "agent unavailable" in result.note
    assert titles(result.page) == ["Fallback event"]


def test_too_many_tool_calls_fall_back(world):
    publish(world, "E")
    graph, _ = agent_graph(world, [[call("list_tags")] * (MAX_TOOL_CALLS + 1)])
    result = graph.search_agent.search(world.carol.id, "x", NOW)
    assert not result.used_agent and "too many tool calls" in result.note


REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("error", [
    anthropic.RateLimitError("slow", response=httpx2.Response(429, request=REQ), body=None),
    anthropic.InternalServerError("x", response=httpx2.Response(500, request=REQ), body=None),
    anthropic.APIConnectionError(request=REQ),
    anthropic.AnthropicError("no credentials"),
])
def test_api_failures_fall_back(world, error):
    publish(world, "E")
    graph, _ = agent_graph(world, [], error=error)
    result = graph.search_agent.search(world.carol.id, "anything", NOW)
    assert not result.used_agent and result.note.startswith("agent unavailable")


# --- tools: privacy and visibility ---------------------------------------------


def tools_for(world, viewer=None, origin=None):
    session = SearchSession(viewer_id=viewer.id if viewer else None, now=NOW, origin=origin)
    g = world.graph
    return SearchTools(g.store, g.discovery, g.preferences, g.activity, g.privacy, session)


def run(tools, name, **tool_input):
    output, is_error = tools.run(name, tool_input)
    return json.loads(output), is_error


def test_tools_only_show_what_the_searcher_may_see(world):
    publish(world, "Members only", visibility=Visibility.MEMBERS)
    publish(world, "Public")
    out, _ = run(tools_for(world, world.carol), "search_events", keywords=None, tags=[],
                 org_name=None, within_days=None, max_distance_km=None, limit=10)
    assert [e["title"] for e in out["events"]] == ["Public"]


def test_tool_output_holds_nothing_identifying(world):
    alice = world.graph.store.node(world.alice.id, Person)
    world.graph.store.save_node(replace(alice, email="alice@northeastern.edu"))
    connect(world, world.carol, world.alice)
    event = publish(world, "Chess", coordinates=GeoPoint(42.3456, -71.0987))
    world.graph.rsvps.respond(event.id, world.alice.id, RsvpStatus.GOING, NOW)
    tools = tools_for(world, world.carol, origin=GeoPoint(42.1111, -71.2222))
    dump = json.dumps([
        run(tools, name, **args)[0] for name, args in [
            ("get_person_overview", {}), ("get_active_orgs", {"limit": 5}),
            ("get_recent_events", {"limit": 5}), ("list_tags", {}),
            ("search_events", {"keywords": None, "tags": [], "org_name": None,
                               "within_days": None, "max_distance_km": None, "limit": 10}),
        ]
    ])
    for secret in ("alice@", "Alice", "Carol", world.alice.id, world.carol.id, event.id,
                   "42.3456", "42.1111"):
        assert secret not in dump
    assert '"friends_going": 1' in dump


def test_history_tools_respect_the_opt_out(world):
    person = world.graph.store.node(world.carol.id, Person)
    world.graph.store.save_node(replace(person, ai_uses_history=False))
    tools = tools_for(world, world.carol)
    assert "no profile" in run(tools, "get_person_overview")[0]["note"]
    assert run(tools, "get_active_orgs", limit=5)[0] == []
    assert run(tools, "get_recent_events", limit=5)[0] == []


def test_search_filters(world):
    near = publish(world, "Near trivia", days=2, coordinates=GeoPoint(42.341, -71.09))
    publish(world, "Far trivia", days=2, coordinates=GeoPoint(42.6, -71.09))
    publish(world, "Late trivia", days=40)
    tools = tools_for(world, world.carol, origin=CAMPUS)
    base = {"keywords": "trivia", "tags": [], "org_name": None, "within_days": None,
            "max_distance_km": None, "limit": 10}
    found = lambda **o: [e["title"] for e in run(tools, "search_events", **{**base, **o})[0]["events"]]
    assert set(found()) == {"Near trivia", "Far trivia", "Late trivia"}
    assert set(found(within_days=7)) == {"Near trivia", "Far trivia"}
    assert found(max_distance_km=2) == ["Near trivia", "Late trivia"] or set(
        found(max_distance_km=2)) == {"Near trivia", "Late trivia"}
    assert found(keywords="pottery") == []
    assert near.id  # sanity


def test_org_filter_matches_names_and_includes_orgs_under_it(world):
    publish(world, "Chapter social")
    tools = tools_for(world, world.carol)
    out, err = run(tools, "search_events", keywords=None, tags=[], org_name="alpha chap",
                   within_days=None, max_distance_km=None, limit=10)
    assert not err and [e["title"] for e in out["events"]] == ["Chapter social"]
    out, err = run(tools, "search_events", keywords=None, tags=[], org_name="nonexistent",
                   within_days=None, max_distance_km=None, limit=10)
    assert err and "no organization" in out["error"]


@pytest.mark.parametrize(("name", "args", "message"), [
    ("get_active_orgs", {"limit": 0}, "between"),
    ("get_active_orgs", {"limit": True}, "whole number"),
    ("get_recent_events", {"limit": "5"}, "whole number"),
    ("search_events", {"limit": 99}, "between"),
    ("search_events", {"limit": 5, "max_distance_km": 3}, "location isn't known"),
    ("search_events", {"limit": 5, "within_days": 0}, "between"),
    ("search_events", {"limit": 5, "keywords": "x" * 201}, "limited"),
    ("search_events", {"limit": 5, "tags": "robotics"}, "list of tag slugs"),
    ("submit_picks", {"goal": "", "picks": []}, "goal is required"),
    ("submit_picks", {"goal": "g", "picks": [{}] * (MAX_PICKS + 1)}, "at most"),
])
def test_tool_inputs_are_validated(world, name, args, message):
    out, is_error = run(tools_for(world, world.carol), name, **args)
    assert is_error and message in out["error"]


def test_picks_need_valid_fit_scores_and_no_duplicates(world):
    publish(world, "E")
    tools = tools_for(world, world.carol)
    run(tools, "search_events", keywords=None, tags=[], org_name=None, within_days=None,
        max_distance_km=None, limit=5)
    for picks, message in [
        ([{"ref": "e1", "fit": 1.5, "reason": ""}], "between"),
        ([{"ref": "e1", "fit": "high", "reason": ""}], "number"),
        ([{"ref": "e1", "fit": 0.5, "reason": ""}] * 2, "twice"),
    ]:
        out, is_error = run(tools, "submit_picks", goal="g", picks=picks)
        assert is_error and message in out["error"]
    assert tools.session.picks is None


@settings(max_examples=150, deadline=None)
@given(st.sampled_from([t["name"] for t in TOOL_DEFINITIONS] + ["nope"]),
       st.dictionaries(st.sampled_from(["limit", "keywords", "tags", "org_name", "within_days",
                                         "max_distance_km", "goal", "picks"]),
                       st.none() | st.booleans() | st.integers(-5, 500) | st.text(max_size=30)
                       | st.floats(allow_nan=True) | st.lists(st.text(max_size=5), max_size=3)))
def test_any_tool_input_returns_json_and_never_raises(tool, tool_input):
    graph = CollectivizeGraph()
    session = SearchSession(viewer_id=None, now=NOW, origin=None)
    tools = SearchTools(graph.store, graph.discovery, graph.preferences, graph.activity,
                        graph.privacy, session)
    output, is_error = tools.run(tool, tool_input)
    json.loads(output)
    assert isinstance(is_error, bool)


# --- optional live run (costs money; off by default) ---------------------------


@pytest.mark.skipif(os.environ.get("COLLECTIVIZE_LIVE_AI") != "1",
                    reason="live API calls; set COLLECTIVIZE_LIVE_AI=1 to run")
def test_live_agent_finds_the_puzzle_event(world):
    publish(world, "Puzzle hunt", description="Fast-paced team puzzles around campus")
    publish(world, "Pottery", description="Slow, relaxing clay work")
    result = world.graph.search_agent.search(world.carol.id, "fast paced puzzles", NOW)
    assert result.used_agent
    assert titles(result.page)[0] == "Puzzle hunt"
