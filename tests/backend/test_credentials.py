"""Finding the API key without it ever living in the repo. The real
Keychain is never touched: subprocess.run is replaced."""

import os
import subprocess
from types import SimpleNamespace

import anthropic
import pytest

from backend import credentials
from backend.graph import CollectivizeGraph

from .conftest import NOW


@pytest.fixture
def keychain(monkeypatch):
    calls = []

    def install(returncode=0, stdout="", error=None):
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if error:
                raise error
            return SimpleNamespace(returncode=returncode, stdout=stdout)
        monkeypatch.setattr(credentials.subprocess, "run", fake_run)
        monkeypatch.setattr(credentials.sys, "platform", "darwin")
        monkeypatch.delenv(credentials.API_KEY_ENV, raising=False)
        return calls
    return install


def test_environment_variable_wins(keychain, monkeypatch):
    calls = keychain(stdout="from-keychain\n")
    monkeypatch.setenv(credentials.API_KEY_ENV, "  from-env  ")
    assert credentials.anthropic_api_key() == "from-env"
    assert calls == []  # Keychain not even asked


def test_keychain_is_used_when_the_variable_is_unset(keychain):
    calls = keychain(stdout="sk-ant-example\n")
    assert credentials.anthropic_api_key() == "sk-ant-example"
    args, kwargs = calls[0]
    assert args == ["/usr/bin/security", "find-generic-password", "-s",
                    credentials.KEYCHAIN_SERVICE, "-w"]
    assert kwargs["timeout"] == credentials.KEYCHAIN_TIMEOUT_SECONDS
    assert "shell" not in kwargs


@pytest.mark.parametrize("kwargs", [
    {"returncode": 44, "stdout": ""},  # not stored
    {"returncode": 0, "stdout": "   \n"},  # empty item
    {"error": FileNotFoundError("no security tool")},
    {"error": subprocess.TimeoutExpired("security", 5)},
])
def test_missing_or_broken_keychain_gives_none(keychain, kwargs):
    keychain(**kwargs)
    assert credentials.anthropic_api_key() is None


def test_other_platforms_only_use_the_environment(keychain, monkeypatch):
    calls = keychain(stdout="never-read")
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    assert credentials.anthropic_api_key() is None
    assert calls == []


@pytest.mark.skipif(os.environ.get("COLLECTIVIZE_LIVE_AI") == "1", reason="live mode")
def test_the_suite_can_never_reach_the_real_api():
    """Guards against tests silently making paid API calls."""
    from backend.graph.claude_interpreter import make_client

    with pytest.raises(anthropic.AnthropicError, match="disabled in tests"):
        make_client()
    graph = CollectivizeGraph()  # default, real interpreter and agent client
    assert not graph.ai_search.search(None, "anything", NOW).used_ai
