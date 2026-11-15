"""Bugs found in AI search, the search agent, its tools and the AI search
script. Fake clients only: no test reaches the API.

Each test asserts the CORRECT behaviour and is marked xfail(strict=True)
while the bug exists, so it starts failing (strictly) once it is fixed."""

import importlib.util
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from backend.graph import CollectivizeGraph
from backend.graph.ai_search import InterpreterError, SearchRequest
from backend.graph.claude_interpreter import ClaudeInterpreter
from backend.objects import (
    Hosts,
    HostRole,
    OrgKind,
    Organization,
    RsvpStatus,
    Visibility,
)

from .conftest import NOW, staff_provenance
from .test_edge_ai import SEARCH_ARGS, candidate, publish, run, tools_for

REPO = Path(__file__).resolve().parents[2]


# --- no credentials: the real SDK raises TypeError, not AnthropicError ---------


@pytest.fixture
def real_sdk_without_credentials(monkeypatch, tmp_path):
    """The real Anthropic client class, with every credential source empty and
    the base URL pointed at a closed local port, so nothing can leave the box."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                 "ANTHROPIC_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path))  # no `ant auth login` profile here
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr("backend.credentials.sys.platform", "linux")  # no keychain
    monkeypatch.setattr(anthropic, "Anthropic", anthropic._client.Anthropic)


@pytest.mark.xfail(strict=True, reason="BUG: no credentials -> TypeError escapes, no fallback")
def test_interpreter_without_credentials_raises_interpreter_error(real_sdk_without_credentials):
    request = SearchRequest(sentence="trivia", today="Fri", viewer=None,
                            candidates=(candidate(ref="e1", title="Trivia"),))
    with pytest.raises(InterpreterError):
        ClaudeInterpreter().interpret(request)


@pytest.mark.xfail(strict=True, reason="BUG: no credentials -> AI search crashes with TypeError")
def test_ai_search_without_credentials_falls_back_to_keywords(world, real_sdk_without_credentials):
    publish(world, "Trivia night")
    graph = CollectivizeGraph(world.graph.store)  # real interpreter, real agent client
    result = graph.ai_search.search(world.carol.id, "trivia", NOW)
    assert not result.used_ai
    assert [i.event.title for i in result.page.items] == ["Trivia night"]


@pytest.mark.xfail(strict=True, reason="BUG: no credentials -> search agent crashes with TypeError")
def test_search_agent_without_credentials_falls_back(world, real_sdk_without_credentials):
    publish(world, "Trivia night")
    graph = CollectivizeGraph(world.graph.store)
    result = graph.search_agent.search(world.carol.id, "trivia", NOW)
    assert not result.used_agent


# --- privacy: hidden co-host orgs leak through the viewer's activity -----------


def _event_cohosted_by_hidden_org(world):
    """A public event (primary host: the public chapter) co-hosted by an org
    Carol may not see; Carol is going."""
    hidden = world.graph.store.save_node(Organization(
        name="Secret Society", kind=OrgKind.CHAPTER, provenance=staff_provenance(),
        visibility=Visibility.PRIVATE,
    ))
    event = publish(world, "Open mic")
    world.graph.store.save_edge(Hosts(
        source_id=hidden.id, target_id=event.id, role=HostRole.CO_HOST, accepted=True,
        provenance=staff_provenance(),
    ))
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    assert not world.graph.privacy.can_view(world.carol.id, hidden)
    return hidden


@pytest.mark.xfail(strict=True, reason="BUG: get_active_orgs names orgs the viewer can't see")
def test_get_active_orgs_hides_orgs_the_viewer_cannot_see(world):
    _event_cohosted_by_hidden_org(world)
    out, is_error = run(tools_for(world, world.carol), "get_active_orgs", limit=10)
    assert not is_error
    assert "Secret Society" not in json.dumps(out)


@pytest.mark.xfail(strict=True, reason="BUG: AI search sends hidden org names to the model")
def test_ai_search_viewer_context_hides_orgs_the_viewer_cannot_see(world):
    from .test_ai_search import FakeInterpreter

    _event_cohosted_by_hidden_org(world)
    fake = FakeInterpreter()
    graph = CollectivizeGraph(world.graph.store, search_interpreter=fake)
    graph.ai_search.search(world.carol.id, "music", NOW)
    viewer = fake.requests[0].viewer
    assert viewer is not None
    assert all(name != "Secret Society" for name, _kind, _member in viewer.top_orgs)


# --- scripts/ai_search.py: agent mode ignores --limit and --radius ------------


def _load_script():
    spec = importlib.util.spec_from_file_location("ai_search_script",
                                                  REPO / "scripts" / "ai_search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_item(title, km):
    return SimpleNamespace(score=0.5, event=SimpleNamespace(title=title), reasons=("h",),
                           distance_km=km)


def _run_script(monkeypatch, caplog, argv):
    script = _load_script()
    items = tuple(_fake_item(f"Pick {i}", km=1.0 + 10 * i) for i in range(5))
    calls = []

    def agent_search(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(goal="g", used_agent=True, note=None, steps=(),
                               page=SimpleNamespace(items=items))

    graph = SimpleNamespace(store=SimpleNamespace(nodes=lambda _t: []),
                            search_agent=SimpleNamespace(search=agent_search))

    @contextmanager
    def fake_open(_config):
        yield graph

    monkeypatch.setattr(script, "load_config",
                        lambda: SimpleNamespace(path=SimpleNamespace(exists=lambda: True)))
    monkeypatch.setattr(script, "open_graph", fake_open)
    with caplog.at_level(logging.INFO, logger="ai_search"):
        assert script.main(argv) == 0
    return [r.getMessage() for r in caplog.records if "Pick" in r.getMessage()]


@pytest.mark.xfail(strict=True, reason="BUG: --limit is ignored in agent mode")
def test_script_agent_mode_honours_limit(monkeypatch, caplog):
    shown = _run_script(monkeypatch, caplog, ["--limit", "1", "anything"])
    assert len(shown) == 1


@pytest.mark.xfail(strict=True, reason="BUG: --radius is ignored in agent mode")
def test_script_agent_mode_honours_radius(monkeypatch, caplog):
    shown = _run_script(monkeypatch, caplog, ["--radius", "5", "anything"])
    assert shown == [m for m in shown if "Pick 0" in m] and shown


# --- sanity: the agent's search tool itself respects visibility --------------


def test_search_events_does_not_list_events_hidden_from_the_viewer(world):
    publish(world, "Members only", visibility=Visibility.MEMBERS)
    out, _ = run(tools_for(world, world.carol), "search_events", **SEARCH_ARGS)
    assert out["count"] == 0


# --- privacy: removed events still described to the agent ---------------------


@pytest.mark.xfail(strict=True, reason="BUG: get_recent_events returns removed events")
def test_get_recent_events_skips_removed_events(world):
    event = publish(world, "Deleted party")
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    world.graph.store.save_node(world.graph.store.node(event.id, type(event)).removed())
    out, is_error = run(tools_for(world, world.carol), "get_recent_events", limit=15)
    assert not is_error
    assert "Deleted party" not in json.dumps(out)


@pytest.mark.xfail(strict=True, reason="BUG: get_recent_events returns events now hidden from the viewer")
def test_get_recent_events_skips_events_the_viewer_can_no_longer_see(world):
    event = publish(world, "Now members only")
    world.graph.rsvps.respond(event.id, world.carol.id, RsvpStatus.GOING, NOW)
    store = world.graph.store
    store.save_node(store.node(event.id, type(event)).with_visibility(Visibility.MEMBERS))
    assert not world.graph.privacy.can_view(world.carol.id, store.node(event.id, type(event)))
    out, is_error = run(tools_for(world, world.carol), "get_recent_events", limit=15)
    assert not is_error
    assert "Now members only" not in json.dumps(out)
