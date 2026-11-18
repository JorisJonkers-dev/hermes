"""Write HERMES_HOME/config.yaml from the image's seed, and refuse to go on with a config that routes outside the tiers.

Run by docker/entrypoint.sh before anything else starts. The config is owned by the image: it is
rewritten on every start, so a change made at runtime does not survive a restart and Hermes can
never start on its own default configuration (whose providers are outside the tier map).

- A tier whose key is optional and unset (tier-strong-open) loses its provider, so the route does not
  exist; the plugin refuses that tier with a clear message.
- The result must still hold every required tier provider, name the tiers backend, and take its
  main model from a tier provider. Otherwise this exits non-zero and Hermes does not start.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REQUIRED_PROVIDERS = (
    "litellm-tier-free",
    "litellm-tier-vibe",
    "litellm-tier-go-cheap",
    "litellm-tier-review",
)
OPTIONAL_PROVIDERS = ("litellm-tier-strong-open",)
BACKEND = "litellm-tiers"


class ConfigError(Exception):
    pass


def render(seed: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    config = dict(seed)  # ruamel's loader hands back plain dicts with typ="safe"
    providers = dict(config.get("providers") or {})
    for name in OPTIONAL_PROVIDERS:
        key_env = (providers.get(name) or {}).get("key_env")
        if name in providers and not environ.get(key_env or ""):
            del providers[name]
    config["providers"] = providers
    check(config)
    return config


def check(config: Mapping[str, Any]) -> None:
    providers = config.get("providers") or {}
    missing = [name for name in REQUIRED_PROVIDERS if name not in providers]
    if missing:
        raise ConfigError(f"the config lacks the tier providers {', '.join(missing)}")
    backend = (config.get("delegation") or {}).get("backend")
    if backend != BACKEND:
        raise ConfigError(f"delegation.backend is {backend!r}, not {BACKEND!r}")
    main = (config.get("model") or {}).get("provider")
    if main not in providers:
        raise ConfigError(f"model.provider {main!r} is not a tier provider")


def main(argv: list[str], environ: Mapping[str, str] = os.environ) -> int:
    seed_path, out_path = Path(argv[1]), Path(argv[2])
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    try:
        config = render(yaml.load(seed_path.read_text()), environ)
    except ConfigError as error:
        print(f"hermes-entrypoint: refusing to start Hermes: {error}", file=sys.stderr)
        return 1
    with out_path.open("w") as handle:
        yaml.dump(config, handle)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
