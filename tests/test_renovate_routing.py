"""The routing log reuses the plugin's writer, and the loop's tiers are the plugin's own."""

import json
import runpy
import sys

import pytest

from loops.renovate_repair import routing


def test_the_log_writes_one_json_line_through_the_sink():
    lines = []
    log = routing.RoutingLog(lines.append, lambda: 12.34567)
    log.attempt(repo="o/a", pr=2, tier="tier-free", attempt=1, outcome="fixed", model="m")
    log.decision(repo="o/a", pr=2, action="merge", update_type="patch", detail="d", dry_run=False)
    log.decision(repo="o/a", pr=2, action="merge", update_type="patch", detail="d", dry_run=True)
    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == [
        "renovate_repair_attempt",
        "renovate_repair_decision",
        "renovate_repair_plan",
    ]
    assert events[0] == {
        "event": "renovate_repair_attempt",
        "ts": 12.346,
        "repo": "o/a",
        "pr": 2,
        "tier": "tier-free",
        "attempt": 1,
        "outcome": "fixed",
        "model": "m",
    }
    assert lines[0] == json.dumps(events[0], sort_keys=True, separators=(",", ":"))


def test_the_default_writer_is_the_plugins_stdout_writer(capsys, serving):
    assert routing.default_sink() is serving.write_line
    routing.RoutingLog(clock=lambda: 1).write("x", a=1)
    assert json.loads(capsys.readouterr().out) == {"event": "x", "ts": 1, "a": 1}


def test_the_fix_tiers_exist_in_the_plugin_and_exclude_claude_and_strong_open(tiers):
    assert routing.FIX_TIERS == ("tier-free", "tier-vibe", "tier-go-cheap")
    assert set(routing.FIX_TIERS) <= set(routing.tier_routes())
    assert routing.tier_routes() is tiers.TIERS
    assert "tier-strong-open" not in routing.FIX_TIERS


def test_the_plugin_loads_on_its_own_when_it_is_not_already_imported(monkeypatch):
    for name in [n for n in sys.modules if n.startswith(routing.PACKAGE)]:
        monkeypatch.delitem(sys.modules, name)
    assert set(routing.tier_routes()) >= set(routing.FIX_TIERS)
    assert routing.default_sink().__name__ == "_stdout_sink"


def test_running_the_package_as_a_module_exits_with_the_cli_status(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["renovate_repair", "--dry-run"])
    monkeypatch.delenv("GITHUB_TOKEN_FILE", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("loops.renovate_repair", run_name="__main__")
    assert exit_info.value.code == 2
    assert "GITHUB_TOKEN_FILE is not set" in capsys.readouterr().err
