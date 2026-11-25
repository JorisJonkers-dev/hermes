"""The decisions of the loop, over the injected GitHub client and fixer.

Per open Renovate pull request of a public repository:

- a major update gets one comment saying it needs a human, and is never merged;
- a patch or minor update whose checks pass is squash-merged;
- one whose checks fail is repaired on its branch, tier by tier (``FIX_TIERS``), at most
  ``max_attempts`` times, merged once its checks pass, and otherwise commented on and left alone;
- one whose checks are still running, or that has no checks, is left for the next run.

A private repository is never read past the listing: it is skipped before its pull requests are
requested. In a dry run nothing is written to GitHub and no fixer runs; the loop prints a plan.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .classify import MAJOR, classify
from .fixer import Fixer
from .github import GitHub, GitHubError
from .models import FAILING, NO_CHECKS, PASSING, PENDING, RENOVATE_LOGIN, Check, FixResult, Outcome, PullRequest, Repo
from .routing import FIX_TIERS, RoutingLog

MAJOR_MARKER = "<!-- renovate-repair:major -->"


def exhausted_marker(sha: str) -> str:
    return f"<!-- renovate-repair:exhausted:{sha} -->"


@dataclass(frozen=True)
class Settings:
    org: str
    only_repos: tuple[str, ...] = ()
    dry_run: bool = False
    tiers: tuple[str, ...] = FIX_TIERS
    max_attempts: int = 3
    max_repairs: int = 2  # pull requests repaired per run: a run must fit its hourly slot
    wait_s: float = 1800
    poll_s: float = 30


def aggregate(checks: Sequence[Check]) -> str:
    if not checks:
        return NO_CHECKS
    if any(c.state == "failure" for c in checks):
        return FAILING
    if any(c.state == "pending" for c in checks):
        return PENDING
    return PASSING


class RepairLoop:
    def __init__(
        self,
        github: GitHub,
        fixer: Fixer,
        log: RoutingLog,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._github = github
        self._fixer = fixer
        self._log = log
        self._settings = settings
        self._sleep = sleep
        self._monotonic = monotonic
        self._repairs = 0

    def run(self) -> list[Outcome]:
        outcomes: list[Outcome] = []
        for repo in self._github.public_repos(self._settings.org):
            if not repo.is_public or repo.archived:
                continue
            if self._settings.only_repos and repo.name not in self._settings.only_repos:
                continue
            try:
                pulls = self._github.open_pulls(repo)
            except GitHubError as error:
                outcomes.append(Outcome(repo.full_name, 0, "error", str(error)))
                continue
            for pull in pulls:
                if pull.author == RENOVATE_LOGIN:
                    outcomes.append(self._safely(repo, pull))
        return outcomes

    def _safely(self, repo: Repo, pull: PullRequest) -> Outcome:
        try:
            return self._handle(repo, pull)
        except GitHubError as error:
            self._log.decision(
                repo=repo.full_name,
                pr=pull.number,
                action="error",
                update_type="unknown",
                detail=str(error),
                dry_run=self._settings.dry_run,
            )
            return Outcome(repo.full_name, pull.number, "error", str(error))

    def _decide(self, repo: Repo, pull: PullRequest, update_type: str, action: str, detail: str) -> Outcome:
        self._log.decision(
            repo=repo.full_name,
            pr=pull.number,
            action=action,
            update_type=update_type,
            detail=detail,
            dry_run=self._settings.dry_run,
        )
        return Outcome(repo.full_name, pull.number, action, detail)

    def _handle(self, repo: Repo, pull: PullRequest) -> Outcome:
        if pull.draft:
            return self._decide(repo, pull, "unknown", "wait", "a draft; left for the next run")
        update_type = classify(pull.title, pull.body, pull.labels)
        if update_type == MAJOR:
            return self._major(repo, pull)
        state = aggregate(self._github.checks(repo, pull.head_sha))
        if state == PASSING:
            return self._merge(repo, pull, update_type)
        if state == FAILING:
            return self._repair(repo, pull, update_type)
        return self._decide(repo, pull, update_type, "wait", f"checks are {state}; left for the next run")

    def _major(self, repo: Repo, pull: PullRequest) -> Outcome:
        if self._github.has_comment(repo, pull.number, MAJOR_MARKER):
            return self._decide(repo, pull, MAJOR, "skip", "a major update; already commented")
        if not self._settings.dry_run:
            self._github.comment(
                repo,
                pull.number,
                f"{MAJOR_MARKER}\nThis is a major update, so it needs a human: the repair loop does not merge it.",
            )
        return self._decide(repo, pull, MAJOR, "comment-major", "a major update needs a human")

    def _merge(self, repo: Repo, pull: PullRequest, update_type: str) -> Outcome:
        if not self._settings.dry_run:
            self._github.merge(repo, pull.number, pull.head_sha)
        return self._decide(repo, pull, update_type, "merge", f"a {update_type} update with passing checks, squash")

    def _repair(self, repo: Repo, pull: PullRequest, update_type: str) -> Outcome:
        if self._github.has_comment(repo, pull.number, exhausted_marker(pull.head_sha)):
            return self._decide(repo, pull, update_type, "skip", "repair already tried on this commit")
        tiers = self._settings.tiers[: self._settings.max_attempts]
        if self._settings.dry_run:
            return self._decide(repo, pull, update_type, "repair", f"would fix on {', '.join(tiers)}, then merge")
        if self._repairs >= self._settings.max_repairs:
            return self._decide(
                repo, pull, update_type, "wait", "this run's repair budget is spent; left for the next run"
            )
        self._repairs += 1
        tried: list[str] = []
        current = pull
        for attempt, tier in enumerate(tiers, start=1):
            failing = [c for c in self._github.checks(repo, current.head_sha) if c.state == "failure"]
            result = self._fixer.fix(repo, current, tier, failing)
            if not result.pushed:
                self._attempt(repo, pull, tier, attempt, "no-fix", result)
                tried.append(f"{tier}: no fix pushed")
                continue
            current = self._github.pull(repo, pull.number)
            state = self._wait_for_checks(repo, current)
            outcome = {PASSING: "fixed", FAILING: "checks-failed"}.get(state, "checks-timeout")
            self._attempt(repo, pull, tier, attempt, outcome, result)
            if state == PASSING:
                self._github.merge(repo, pull.number, current.head_sha)
                outcome_record = self._decide(repo, pull, update_type, "fix-and-merge", f"fixed on {tier}, then merged")
                outcome_record.attempts = [*tried, f"{tier}: fixed"]
                return outcome_record
            tried.append(f"{tier}: pushed a fix, checks {state}")
            if state != FAILING:
                break
        return self._give_up(repo, pull, update_type, tried)

    def _give_up(self, repo: Repo, pull: PullRequest, update_type: str, tried: list[str]) -> Outcome:
        sha = self._github.pull(repo, pull.number).head_sha
        listing = "\n".join(f"- {line}" for line in tried) or "- nothing was attempted"
        self._github.comment(
            repo,
            pull.number,
            f"{exhausted_marker(sha)}\nThe repair loop could not fix this update and has stopped. Tried:\n{listing}\n"
            "It needs a human.",
        )
        result = self._decide(repo, pull, update_type, "give-up", "no tier fixed the checks; commented")
        result.attempts = tried
        return result

    def _attempt(self, repo: Repo, pull: PullRequest, tier: str, attempt: int, outcome: str, result: FixResult) -> None:
        # The detail goes to the routing log only. It can hold tool output, so it is never put in a comment.
        self._log.attempt(
            repo=repo.full_name,
            pr=pull.number,
            tier=tier,
            attempt=attempt,
            outcome=outcome,
            model=result.model,
            detail=result.detail,
        )

    def _wait_for_checks(self, repo: Repo, pull: PullRequest) -> str:
        """Poll until the checks of ``pull``'s head finish, or ``wait_s`` passes. Returns the state."""
        deadline = self._monotonic() + self._settings.wait_s
        while True:
            state = aggregate(self._github.checks(repo, pull.head_sha))
            if state in (PASSING, FAILING):
                return state
            if self._monotonic() >= deadline:
                return PENDING
            self._sleep(self._settings.poll_s)
