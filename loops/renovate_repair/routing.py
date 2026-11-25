"""The routing log: the plugin's own writer, so the loop's lines land where the serving-model lines do.

The plugin lives in a directory whose name is not an importable module name
(``plugins/delegation-litellm-tiers``) and whose ``__init__`` needs Hermes, so its pure modules
are loaded as a package of their own, without running ``__init__``. In the image the layout is the
repository's: ``loops/`` and ``plugins/`` side by side below ``/opt/hermes-seed``.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

PACKAGE = "delegation_litellm_tiers"
PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "delegation-litellm-tiers"

#: A fix is attempted on these tiers in this order and on no other: never Claude, never strong-open.
FIX_TIERS = ("tier-free", "tier-vibe", "tier-go-cheap")


def _plugin_module(name: str) -> types.ModuleType:
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(PLUGIN_DIR)]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")


def tier_routes() -> dict[str, Any]:
    """The plugin's tier table (tier name to ``TierRoute``)."""
    return _plugin_module("tiers").TIERS


def default_sink() -> Callable[[str], None]:
    """The plugin's log writer: one line to stdout, flushed."""
    return _plugin_module("serving").write_line


class RoutingLog:
    """Writes one JSON line per event through the plugin's writer. Never carries a prompt or a key."""

    def __init__(self, sink: Callable[[str], None] | None = None, clock: Callable[[], float] = time.time) -> None:
        self._sink = sink or default_sink()
        self._clock = clock

    def write(self, event: str, **fields: Any) -> dict[str, Any]:
        line = {"event": event, "ts": round(self._clock(), 3), **fields}
        self._sink(json.dumps(line, sort_keys=True, separators=(",", ":")))
        return line

    def attempt(
        self, *, repo: str, pr: int, tier: str, attempt: int, outcome: str, model: str | None, detail: str = ""
    ) -> None:
        self.write(
            "renovate_repair_attempt",
            repo=repo,
            pr=pr,
            tier=tier,
            attempt=attempt,
            outcome=outcome,
            model=model,
            detail=detail[:300],
        )

    def decision(self, *, repo: str, pr: int, action: str, update_type: str, detail: str, dry_run: bool) -> None:
        event = "renovate_repair_plan" if dry_run else "renovate_repair_decision"
        self.write(event, repo=repo, pr=pr, action=action, update_type=update_type, detail=detail)
