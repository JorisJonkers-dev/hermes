"""The update type is read from labels, a declared line, the change tables and the title; unknown is major."""

import pytest

from loops.renovate_repair.classify import classify

ARROW = "→"


@pytest.mark.parametrize(
    ("title", "body", "labels", "expected"),
    [
        ("Update foo", "", ["type: patch"], "patch"),
        ("Update foo", "", ["Minor"], "minor"),
        ("Update foo", "", ["dependencies", "major"], "major"),
        ("Update foo", "Update type: minor", [], "minor"),
        ("Update foo", "Update type: `digest`", [], "patch"),
        ("Update foo", "Update type: pin", [], "patch"),
        ("Update foo", "| foo | `1.2.3` -> `1.2.4` |", [], "patch"),
        ("Update foo", f"| foo | `v1.2.3` {ARROW} `v1.3.0` |", [], "minor"),
        ("Update foo", "| foo | `1.2.3` -> `2.0.0` |", [], "major"),
        ("Update foo", "| foo | `1.2.3` -> `1.2.2` |", [], "major"),  # not an upgrade: no signal
        ("Update foo", "| foo | `0.4.1` -> `0.5.0` |", [], "major"),  # 0.x minor may break
        ("Update foo", "| foo | `0.4.1` -> `0.4.2` |", [], "patch"),
        ("Update foo", "| foo | `3` -> `3.0.1-rc1` |", [], "patch"),
        ("Update foo from 1.2.3 to 1.3.0", "", [], "minor"),
        ("Revert foo from 2.0.0 to 1.0.0", "", [], "major"),
        ("Update foo", "| a | `1.0.0` -> `1.0.1` |\n| b | `2.0.0` -> `3.0.0` |", [], "major"),
        ("Update foo", "Update type: patch", ["major"], "major"),
        ("Update dependency foo to v2", "", [], "major"),  # nothing readable: a human
    ],
)
def test_the_update_type(title, body, labels, expected):
    assert classify(title, body, labels) == expected
