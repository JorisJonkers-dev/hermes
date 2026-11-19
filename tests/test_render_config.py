"""docker/render_config.py: the config Hermes starts with is the image's, and it always routes through the tiers."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "config" / "config.yaml"
ENV = {
    "LITELLM_FREE_KEY": "k",
    "LITELLM_TIER_VIBE_KEY": "k",
    "LITELLM_TIER_GO_CHEAP_KEY": "k",
    "LITELLM_TIER_REVIEW_KEY": "k",
    "LITELLM_TIER_STRONG_OPEN_KEY": "k",
}


@pytest.fixture(scope="module")
def render_config():
    spec = importlib.util.spec_from_file_location("render_config", ROOT / "docker" / "render_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed():
    return yaml.safe_load(SEED.read_text())


def test_every_tier_has_a_provider_that_reads_its_key_and_sends_the_session_header(seed, tiers):
    for route in tiers.TIERS.values():
        provider = seed["providers"][route.provider]
        assert (provider["key_env"], provider["default_model"], provider["session_affinity_header"]) == (
            route.key_env,
            route.tier,
            "x-hermes-session",
        )


def test_the_renderer_requires_exactly_the_tiers_whose_key_is_not_optional(render_config, tiers):
    required = {r.provider for t, r in tiers.TIERS.items() if t not in tiers.OPTIONAL_TIERS}
    optional = {r.provider for t, r in tiers.TIERS.items() if t in tiers.OPTIONAL_TIERS}
    assert (set(render_config.REQUIRED_PROVIDERS), set(render_config.OPTIONAL_PROVIDERS)) == (required, optional)


def test_the_strong_open_provider_is_dropped_when_its_key_is_not_set(render_config, seed):
    env = {k: v for k, v in ENV.items() if k != "LITELLM_TIER_STRONG_OPEN_KEY"}
    assert "litellm-tier-strong-open" not in render_config.render(seed, env)["providers"]


def test_the_strong_open_provider_is_kept_when_its_key_is_set(render_config, seed):
    assert "litellm-tier-strong-open" in render_config.render(seed, ENV)["providers"]


def test_a_seed_without_the_strong_open_provider_renders_unchanged(render_config, seed):
    del seed["providers"]["litellm-tier-strong-open"]
    assert render_config.render(seed, {}) == seed


def test_hermes_default_config_is_refused(render_config):
    with pytest.raises(render_config.ConfigError, match="lacks the tier providers"):
        render_config.render({}, ENV)


def test_a_config_without_the_tiers_backend_is_refused(render_config, seed):
    seed["delegation"]["backend"] = "default"
    with pytest.raises(render_config.ConfigError, match="delegation.backend"):
        render_config.render(seed, ENV)


def test_a_main_model_outside_the_tier_providers_is_refused(render_config, seed):
    seed["model"]["provider"] = "openrouter"
    with pytest.raises(render_config.ConfigError, match="model.provider"):
        render_config.render(seed, ENV)


def test_the_config_is_written_for_hermes_to_read(render_config, tmp_path):
    out = tmp_path / "config.yaml"
    assert render_config.main(["x", str(SEED), str(out)], ENV) == 0
    assert yaml.safe_load(out.read_text())["delegation"]["backend"] == "litellm-tiers"


def test_a_refused_config_writes_nothing_and_exits_non_zero(render_config, tmp_path, capsys):
    seed = tmp_path / "seed.yaml"
    seed.write_text("model: {provider: openrouter}\n")
    out = tmp_path / "config.yaml"
    assert render_config.main(["x", str(seed), str(out)], ENV) == 1
    assert not out.exists()
    assert "refusing to start Hermes" in capsys.readouterr().err
