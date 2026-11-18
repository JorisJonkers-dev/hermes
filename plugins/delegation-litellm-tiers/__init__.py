"""Delegation provider that maps a ``tier`` per subagent task to a LiteLLM tier group.

Why a plugin: Hermes ignores a per-call model override (NousResearch/hermes-agent#17685), so the
model of a subagent cannot be picked by asking for one. A delegation provider answers "which route
does this task take" before the child is built, and core enforces the ``tier`` enum first.

Set ``delegation.backend: litellm-tiers`` and enable the plugin (``config/config.yaml`` does both).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.delegation_provider import DelegationProvider, DelegationRouteError

from .serving import ServingRecorder, install_header_capture
from .tiers import DEFAULT_TIER, TIERS, UnknownTierError, route_for

BACKEND_NAME = "litellm-tiers"


class LiteLLMTiers(DelegationProvider):
    @property
    def name(self) -> str:
        return BACKEND_NAME

    @property
    def display_name(self) -> str:
        return "LiteLLM tiers"

    @property
    def summary(self) -> str:
        return "Each subagent task names a LiteLLM tier; the model is the tier's, never the agent's."

    def task_parameters(self, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "tier": {
                "type": "string",
                "enum": list(TIERS),
                "description": "; ".join(f"{r.tier}: {r.description}" for r in TIERS.values()),
            }
        }

    def description(self, config: Mapping[str, Any]) -> str:
        return f"Set `tier` per task to pick what it runs on. Without one, a task runs on {DEFAULT_TIER}."

    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        requested = task.get("tier", DEFAULT_TIER)
        try:
            route = route_for(requested)
        except UnknownTierError as error:
            raise DelegationRouteError(str(error)) from error
        return {
            "provider": route.provider,
            "model": route.model,
            "label": route.tier,
            # A tier's own chain is LiteLLM's. Never fall back to the parent's or another provider.
            "fallback_providers": [],
        }


def register(ctx: Any) -> None:
    import httpx

    recorder = ServingRecorder()
    install_header_capture(recorder, httpx)
    ctx.register_delegation_provider(LiteLLMTiers())
    ctx.register_hook("subagent_start", lambda **kw: recorder.note_subagent(kw.get("child_session_id")))
    ctx.register_hook("post_api_request", lambda **kw: recorder.record_call(**kw))
