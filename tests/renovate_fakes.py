"""An in-memory GitHub and a scripted fixer for the Renovate repair loop."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from loops.renovate_repair.github import GitHubError
from loops.renovate_repair.models import Check, FixResult, PullRequest, Repo


def make_pull(
    number=1, title="Update dependency foo to v1.2.4", body="", labels=("patch",), author="renovate[bot]", sha="s1"
):
    return PullRequest(number, title, body, tuple(labels), author, sha, f"renovate/pr-{number}", "JorisJonkers-dev/app")


def make_repo(name="app", private=False, visibility="public", archived=False):
    return Repo("JorisJonkers-dev", name, private, visibility, archived)


@dataclass
class FakeGitHub:
    repos: list[Repo] = field(default_factory=list)
    pulls: dict[str, list[PullRequest]] = field(default_factory=dict)
    #: head sha -> checks
    checks_by_sha: dict[str, list[Check]] = field(default_factory=dict)
    comments: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    writes: list[tuple] = field(default_factory=list)
    fail_merge: bool = False
    fail_listing: set[str] = field(default_factory=set)

    def public_repos(self, org):
        return list(self.repos)

    def open_pulls(self, repo):
        self.reads.append(repo.full_name)
        if repo.name in self.fail_listing:
            raise GitHubError("listing failed")
        return list(self.pulls.get(repo.name, []))

    def pull(self, repo, number):
        return next(p for p in self.pulls[repo.name] if p.number == number)

    def checks(self, repo, sha):
        return list(self.checks_by_sha.get(sha, []))

    def has_comment(self, repo, number, marker):
        return any(marker in body for body in self.comments.get((repo.name, number), []))

    def comment(self, repo, number, body):
        self.writes.append(("comment", repo.name, number, body))
        self.comments.setdefault((repo.name, number), []).append(body)

    def merge(self, repo, number, sha):
        if self.fail_merge:
            raise GitHubError("merge failed with status 405")
        self.writes.append(("merge", repo.name, number, sha))

    def push_new_head(self, repo, number, sha, checks):
        """A fix lands on the branch: the head moves and the new head has ``checks``."""
        self.pulls[repo.name] = [replace(p, head_sha=sha) if p.number == number else p for p in self.pulls[repo.name]]
        self.checks_by_sha[sha] = checks


@dataclass
class ScriptedFixer:
    """Each call takes the next step: ``(pushed, new sha, checks of the new head)``."""

    github: FakeGitHub
    steps: Sequence[tuple[bool, str, list[Check]]]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def fix(self, repo, pull, tier, failing):
        self.calls.append((tier, pull.head_sha))
        pushed, sha, checks = self.steps[len(self.calls) - 1]
        if pushed:
            self.github.push_new_head(repo, pull.number, sha, checks)
        return FixResult(pushed, "scripted", f"model-of-{tier}" if pushed else None)


OK = [Check("Pipeline Complete", "success")]
BAD = [Check("Pipeline Complete", "failure", "tests fail")]
RUNNING = [Check("Pipeline Complete", "pending")]
