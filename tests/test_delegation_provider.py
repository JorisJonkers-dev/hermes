"""The delegation provider: what route a subagent task takes, and which tasks are refused."""

import pytest


@pytest.fixture
def provider(plugin):
    return plugin.LiteLLMTiers()


def test_an_unknown_tier_is_refused_with_a_message_the_model_can_act_on(provider, route_error):
    with pytest.raises(route_error, match="Unknown tier 'tier-premium'.*No subagent was started"):
        provider.route_task({"goal": "x", "tier": "tier-premium"}, {})


@pytest.mark.parametrize(
    ("tier", "model"),
    [
        ("tier-free", "tier-free"),
        ("tier-vibe", "tier-vibe"),
        ("tier-go-cheap", "tier-go-cheap"),
        ("tier-strong-open", "tier-strong-open"),
        ("tier-review", "tier-review"),
    ],
)
def test_a_tier_routes_to_its_provider_with_the_tier_as_the_model(provider, tier, model):
    route = provider.route_task({"goal": "x", "tier": tier}, {})
    assert (route["provider"], route["model"], route["label"]) == (f"litellm-{tier}", model, tier)


def test_a_route_never_falls_back_outside_the_tier(provider):
    assert provider.route_task({"goal": "x", "tier": "tier-vibe"}, {})["fallback_providers"] == []


def test_a_task_without_a_tier_runs_on_the_free_tier(provider):
    assert provider.route_task({"goal": "x"}, {})["provider"] == "litellm-tier-free"


def test_the_tier_parameter_is_an_enum_of_the_five_tiers(provider):
    schema = provider.task_parameters({})["tier"]
    assert schema["enum"] == ["tier-free", "tier-vibe", "tier-go-cheap", "tier-strong-open", "tier-review"]


def test_the_tier_parameter_is_the_same_bytes_for_the_same_config(provider):
    assert provider.task_parameters({}) == provider.task_parameters({"backends": {"x": 1}})


def test_the_backend_is_selected_by_the_name_the_config_uses(provider):
    assert provider.name == "litellm-tiers"


def test_the_picker_texts_name_the_backend_and_the_default_tier(provider):
    assert provider.display_name == "LiteLLM tiers"
    assert "tier" in provider.summary
    assert "tier-free" in provider.description({})
