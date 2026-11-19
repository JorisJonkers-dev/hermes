"""The small value types the loop passes around. Plain data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field

RENOVATE_LOGIN = "renovate[bot]"

#: What a pull request's checks add up to.
PASSING = "passing"
FAILING = "failing"
PENDING = "pending"
NO_CHECKS = "none"


@dataclass(frozen=True)
class Repo:
    owner: str
    name: str
    private: bool
    visibility: str
    archived: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def is_public(self) -> bool:
        """Public only when both fields say so: a missing or odd value is not public."""
        return not self.private and self.visibility == "public"


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    author: str
    head_sha: str
    head_ref: str
    head_repo: str  # full name of the repository the branch lives in; differs from the base for a fork
    draft: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    state: str  # "success", "failure" or "pending"
    detail: str = ""


@dataclass(frozen=True)
class FixResult:
    """What one repair attempt did. ``pushed`` is true only when a commit reached the branch."""

    pushed: bool
    detail: str
    model: str | None = None


@dataclass
class Outcome:
    """What the loop did with one pull request, for the run summary."""

    repo: str
    number: int
    action: str
    detail: str = ""
    attempts: list[str] = field(default_factory=list)
