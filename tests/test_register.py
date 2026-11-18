"""register(): what the plugin hands Hermes."""

import json
from pathlib import Path

import httpx
import pytest


class Ctx:
    def __init__(self):
        self.providers = []
        self.hooks = {}

    def register_delegation_provider(self, provider):
        self.providers.append(provider)

    def register_hook(self, name, callback):
        self.hooks[name] = callback


@pytest.fixture
def registered(plugin, capsys):
    original = (httpx.Client.send, httpx.AsyncClient.send)
    ctx = Ctx()
    plugin.register(ctx)
    yield ctx
    httpx.Client.send, httpx.AsyncClient.send = original


def test_the_plugin_registers_the_tiers_backend(registered):
    assert [p.name for p in registered.providers] == ["litellm-tiers"]


def test_a_subagents_call_is_logged_through_the_hooks_hermes_fires(registered, capsys):
    registered.hooks["subagent_start"](child_session_id="child-9", parent_session_id="p")
    registered.hooks["post_api_request"](
        session_id="child-9", provider="litellm-tier-review", model="tier-review", response_model="gemma-4-31b"
    )
    entry = json.loads(capsys.readouterr().out)
    assert (entry["subagent"], entry["tier"], entry["response_model"]) == (True, "tier-review", "gemma-4-31b")


def test_the_manifest_declares_a_backend_named_like_its_directory(plugin):
    text = (Path(plugin.__path__[0]) / "plugin.yaml").read_text()
    assert "name: delegation-litellm-tiers" in text
    assert "kind: backend" in text
