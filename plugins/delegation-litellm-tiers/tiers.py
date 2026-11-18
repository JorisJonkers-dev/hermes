"""The tier enum and what each tier means in this deployment.

Pure data and lookups, with no Hermes import, so it is testable on its own.

A tier is the name of a routing group on the LiteLLM proxy (``catalog/routing.json``
``tier_groups`` in JorisJonkers-dev/litellm). Hermes reaches each tier through its own custom
provider (``providers:`` in ``config/config.yaml``), because a LiteLLM virtual key holds one
tier's chain and nothing else. The route a subagent takes is therefore a provider plus the tier
name as the model; the model is never chosen by the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

PROVIDER_PREFIX = "litellm-"


@dataclass(frozen=True)
class TierRoute:
    """One tier: the Hermes provider that holds its key, and the LiteLLM model name to call."""

    tier: str
    provider: str
    model: str
    description: str


def _route(tier: str, description: str) -> TierRoute:
    return TierRoute(tier=tier, provider=f"{PROVIDER_PREFIX}{tier}", model=tier, description=description)


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


def route_for(tier: object) -> TierRoute:
    """The route of ``tier``; :class:`UnknownTierError` for anything that is not a known tier name."""
    if isinstance(tier, str) and tier in TIERS:
        return TIERS[tier]
    known = ", ".join(TIERS)
    raise UnknownTierError(f"Unknown tier {tier!r}. Use one of: {known}. No subagent was started.")


def tier_of_provider(provider: object) -> str | None:
    """The tier a Hermes provider name belongs to, or ``None`` for a provider that is not a tier's."""
    if not isinstance(provider, str):
        return None
    for route in TIERS.values():
        if route.provider == provider:
            return route.tier
    return None
