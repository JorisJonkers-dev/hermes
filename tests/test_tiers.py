"""The tier enum: which tiers exist, what each maps to, and that nothing else is accepted."""

import pytest

TIER_NAMES = ["tier-free", "tier-vibe", "tier-go-cheap", "tier-strong-open", "tier-review"]


def test_the_tier_enum_is_exactly_the_five_litellm_tiers(tiers):
    assert list(tiers.TIERS) == TIER_NAMES


@pytest.mark.parametrize("tier", TIER_NAMES)
def test_a_tier_maps_to_its_own_provider_and_to_the_litellm_group_of_the_same_name(tiers, tier):
    route = tiers.route_for(tier)
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
