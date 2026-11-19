"""The tier enum and what each tier means in this deployment.

Pure data and lookups, with no Hermes import, so it is testable on its own.

A tier is the name of a routing group on the LiteLLM proxy (``catalog/routing.json``
``tier_groups`` in JorisJonkers-dev/litellm). Hermes reaches each tier through its own custom
provider (``providers:`` in ``config/config.yaml``), because a LiteLLM virtual key holds one
tier's chain and nothing else. The route a subagent takes is therefore a provider plus the tier
name as the model; the model is never chosen by the agent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

PROVIDER_PREFIX = "litellm-"


@dataclass(frozen=True)
class TierRoute:
    """One tier: the Hermes provider that holds its key, and the LiteLLM model name to call."""

    tier: str
    provider: str
    model: str
    description: str
    key_env: str


#: The environment variable that holds each tier's LiteLLM key (deploy/env/hermes/base.env).
KEY_ENVS = {
    "tier-free": "LITELLM_FREE_KEY",
    "tier-vibe": "LITELLM_TIER_VIBE_KEY",
    "tier-go-cheap": "LITELLM_TIER_GO_CHEAP_KEY",
    "tier-strong-open": "LITELLM_TIER_STRONG_OPEN_KEY",
    "tier-review": "LITELLM_TIER_REVIEW_KEY",
}

#: Tiers whose key may be absent. Without it the provider is not configured (docker/render_config.py)
#: and the tier is refused.
OPTIONAL_TIERS = frozenset({"tier-strong-open"})


def _route(tier: str, description: str) -> TierRoute:
    return TierRoute(
        tier=tier,
        provider=f"{PROVIDER_PREFIX}{tier}",
        model=tier,
        description=description,
        key_env=KEY_ENVS[tier],
    )


#: The closed tier enum, cheapest first. Adding a tier is a change to this table, to
#: ``config/config.yaml`` (its provider) and to the Vault grant in ``deploy/hermes.project.yml``.
TIERS: dict[str, TierRoute] = {
    route.tier: route
    for route in (
        _route("tier-free", "Free models only. Public work; the provider may train on the prompt."),
        _route("tier-vibe", "Vibe Flash first, then Go Flash. Routine implementation."),
        _route("tier-go-cheap", "OpenCode Go Flash models. Routine work when Vibe is spent."),
        _route("tier-strong-open", "A stronger open model. Escalation only, when the cheaper tiers failed."),
        _route("tier-review", "Free models of several labs first, then Flash models. Reviews and red-teaming."),
    )
}

#: A task that names no tier takes this one. The cheapest, and free by construction, so an
#: omitted tier can never spend.
DEFAULT_TIER = "tier-free"


class UnknownTierError(ValueError):
    """The tier is not in :data:`TIERS`."""


class TierUnavailableError(ValueError):
    """The tier exists, but this deployment holds no key for it."""


def route_for(tier: object, environ: Mapping[str, str] | None = None) -> TierRoute:
    """The route of ``tier``.

    :class:`UnknownTierError` for anything that is not a tier name; :class:`TierUnavailableError`
    for an optional tier whose key is not set in ``environ`` (the process environment by default).
    """
    if not (isinstance(tier, str) and tier in TIERS):
        known = ", ".join(TIERS)
        raise UnknownTierError(f"Unknown tier {tier!r}. Use one of: {known}. No subagent was started.")
    route = TIERS[tier]
    env = os.environ if environ is None else environ
    if tier in OPTIONAL_TIERS and not env.get(route.key_env):
        raise TierUnavailableError(
            f"{tier} is not enabled in this deployment: its key is not configured. "
            "Use a different tier. No subagent was started."
        )
    return route


def tier_of_provider(provider: object) -> str | None:
    """The tier a Hermes provider name belongs to, or ``None`` for a provider that is not a tier's."""
    if not isinstance(provider, str):
        return None
    for route in TIERS.values():
        if route.provider == provider:
            return route.tier
    return None
