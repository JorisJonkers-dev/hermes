"""Read the update type of a Renovate pull request: patch, minor or major.

The signals: Renovate's labels, an ``Update type: <x>`` line (at the start of a line), the version pairs
of the body's change tables (```1.2.3` -> `1.2.4```), and the old and new version of the title. The
highest type found wins, so a grouped pull request is as risky as its riskiest member. A pull
request that shows no signal at all is a ``major``: an unreadable update goes to a human, it is not
merged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PATCH, MINOR, MAJOR = "patch", "minor", "major"
_RANK = {PATCH: 0, MINOR: 1, MAJOR: 2}

_LABEL = re.compile(r"\b(major|minor|patch)\b", re.I)
_DECLARED = re.compile(r"^[ \t>*-]*update type:\s*`?(major|minor|patch|digest|pin)`?", re.I | re.M)
#: A version, and not the leading digits of a word (a digest such as ``1a2b...`` is not one).
_VERSION = r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?!\w)"
_PAIR = re.compile(rf"`{_VERSION}[^`]*`\s*(?:->|→)\s*`{_VERSION}[^`]*`")
_TITLE_PAIR = re.compile(rf"\bfrom\s+{_VERSION}\S*\s+to\s+{_VERSION}", re.I)
#: A digest or a pin changes no version, so the update is as small as a patch.
_AS_PATCH = {"digest", "pin"}


def _highest(found: Iterable[str]) -> str | None:
    return max(found, key=_RANK.__getitem__, default=None)


def _bump(old: tuple[str, ...], new: tuple[str, ...]) -> str | None:
    """The type of the step from ``old`` to ``new`` (three version groups each, ``''`` when absent).

    ``None`` when the version does not go up. On 0.x, a minor step may break, so it counts as major.
    """
    a = tuple(int(part or 0) for part in old)
    b = tuple(int(part or 0) for part in new)
    if b <= a:
        return None
    if b[0] != a[0] or (a[0] == 0 and b[1] != a[1]):
        return MAJOR
    if b[1] != a[1]:
        return MINOR
    return PATCH


def classify(title: str, body: str, labels: Iterable[str]) -> str:
    found = [m.group(1).lower() for label in labels for m in _LABEL.finditer(label)]
    for match in _DECLARED.finditer(body):
        kind = match.group(1).lower()
        found.append(PATCH if kind in _AS_PATCH else kind)
    for match in _PAIR.finditer(body):
        step = _bump(match.groups()[:3], match.groups()[3:])
        if step:
            found.append(step)
    for match in _TITLE_PAIR.finditer(title):
        step = _bump(match.groups()[:3], match.groups()[3:])
        if step:
            found.append(step)
    return _highest(found) or MAJOR
