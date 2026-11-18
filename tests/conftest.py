"""Load the plugin the way Hermes does: as a package named after its directory.

Hermes is not installed for these tests (the plugin is pure Python over a small interface), so
``agent.delegation_provider`` is stood in by a copy of the interface of Hermes v0.23.2
(agent/delegation_provider.py): ``DelegationProvider.settings`` and ``DelegationRouteError``
are the parts the plugin relies on. If Hermes changes them, the image build's smoke test
(scripts/smoke.sh) is what catches it.
"""

from __future__ import annotations

import abc
import importlib.util
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "delegation-litellm-tiers"
PACKAGE = "delegation_litellm_tiers"


class DelegationRouteError(ValueError):
    pass


class DelegationProvider(abc.ABC):
    def settings(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    @abc.abstractmethod
    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]: ...


def _install_hermes_stub() -> None:
    agent = types.ModuleType("agent")
    agent.__path__ = []  # a package
    provider = types.ModuleType("agent.delegation_provider")
    provider.DelegationProvider = DelegationProvider  # type: ignore[attr-defined]
    provider.DelegationRouteError = DelegationRouteError  # type: ignore[attr-defined]
    sys.modules["agent"] = agent
    sys.modules["agent.delegation_provider"] = provider


def _load_plugin() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_install_hermes_stub()
_PLUGIN = _load_plugin()


@pytest.fixture
def plugin() -> types.ModuleType:
    return _PLUGIN


@pytest.fixture
def tiers() -> types.ModuleType:
    return sys.modules[f"{PACKAGE}.tiers"]


@pytest.fixture
def serving() -> types.ModuleType:
    return sys.modules[f"{PACKAGE}.serving"]


@pytest.fixture
def route_error() -> type[Exception]:
    return DelegationRouteError
