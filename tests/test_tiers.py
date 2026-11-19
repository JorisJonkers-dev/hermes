"""The tier enum: which tiers exist, what each maps to, and that nothing else is accepted."""

import pytest

TIER_NAMES = ["tier-free", "tier-vibe", "tier-go-cheap", "tier-strong-open", "tier-review"]


def test_the_tier_enum_is_exactly_the_five_litellm_tiers(tiers):
    assert list(tiers.TIERS) == TIER_NAMES


@pytest.mark.parametrize("tier", TIER_NAMES)
def test_a_tier_maps_to_its_own_provider_and_to_the_litellm_group_of_the_same_name(tiers, tier):
    route = tiers.route_for(tier, {"LITELLM_TIER_STRONG_OPEN_KEY": "k"})
    assert (route.provider, route.model) == (f"litellm-{tier}", tier)


@pytest.mark.parametrize("tier", ["tier-premium", "free", "", "TIER-FREE", None, 3, ["tier-free"]])
def test_an_unknown_tier_is_refused_and_the_message_lists_the_valid_ones(tiers, tier):
    with pytest.raises(tiers.UnknownTierError, match="tier-free, tier-vibe, tier-go-cheap"):
        tiers.route_for(tier)


def test_a_task_that_names_no_tier_would_take_the_free_one(tiers):
    assert tiers.DEFAULT_TIER == "tier-free"


def test_a_tier_provider_names_its_tier(tiers):
    assert tiers.tier_of_provider("litellm-tier-review") == "tier-review"


@pytest.mark.parametrize("provider", ["openrouter", "litellm-tier-nope", None, 7])
def test_a_provider_that_is_not_a_tiers_names_no_tier(tiers, provider):
    assert tiers.tier_of_provider(provider) is None


ALL_KEYS = {
    "LITELLM_FREE_KEY": "k",
    "LITELLM_TIER_VIBE_KEY": "k",
    "LITELLM_TIER_GO_CHEAP_KEY": "k",
    "LITELLM_TIER_STRONG_OPEN_KEY": "k",
    "LITELLM_TIER_REVIEW_KEY": "k",
}


def test_each_tier_names_the_environment_variable_of_its_key(tiers):
    assert {t: r.key_env for t, r in tiers.TIERS.items()} == {
        "tier-free": "LITELLM_FREE_KEY",
        "tier-vibe": "LITELLM_TIER_VIBE_KEY",
        "tier-go-cheap": "LITELLM_TIER_GO_CHEAP_KEY",
        "tier-strong-open": "LITELLM_TIER_STRONG_OPEN_KEY",
        "tier-review": "LITELLM_TIER_REVIEW_KEY",
    }


def test_the_strong_open_tier_is_refused_without_its_key_and_the_message_says_why(tiers):
    env = {k: v for k, v in ALL_KEYS.items() if k != "LITELLM_TIER_STRONG_OPEN_KEY"}
    with pytest.raises(tiers.TierUnavailableError, match="tier-strong-open is not enabled.*key is not configured"):
        tiers.route_for("tier-strong-open", env)


def test_the_strong_open_tier_is_served_with_its_key(tiers):
    assert tiers.route_for("tier-strong-open", ALL_KEYS).provider == "litellm-tier-strong-open"


def test_a_required_tier_is_not_refused_for_a_missing_key_here(tiers):
    assert tiers.route_for("tier-vibe", {}).tier == "tier-vibe"


def test_the_process_environment_is_the_default_source_of_keys(tiers, monkeypatch):
    monkeypatch.delenv("LITELLM_TIER_STRONG_OPEN_KEY", raising=False)
    with pytest.raises(tiers.TierUnavailableError):
        tiers.route_for("tier-strong-open")
