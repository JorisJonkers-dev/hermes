"""The decisions of the Renovate repair loop, over a fixture repository in a fake GitHub."""

import json

import pytest
from renovate_fakes import BAD, OK, RUNNING, FakeGitHub, ScriptedFixer, make_pull, make_repo

from loops.renovate_repair.loop import MAJOR_MARKER, RepairLoop, Settings, aggregate, exhausted_marker
from loops.renovate_repair.models import Check
from loops.renovate_repair.routing import RoutingLog


class Lines:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(json.loads(line))

    def of(self, event):
        return [line for line in self.lines if line["event"] == event]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def build(github, steps=(), **settings):
    lines, clock = Lines(), Clock()
    fixer = ScriptedFixer(github, steps)
    loop = RepairLoop(
        github,
        fixer,
        RoutingLog(lines, lambda: 1.5),
        Settings(org="JorisJonkers-dev", **settings),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return loop, fixer, lines, clock


def fixture_repo(pull, checks, repo=None):
    repo = repo or make_repo()
    return FakeGitHub(repos=[repo], pulls={repo.name: [pull]}, checks_by_sha={pull.head_sha: checks})


def test_a_patch_with_passing_checks_is_squash_merged():
    github = fixture_repo(make_pull(), OK)
    loop, fixer, lines, _ = build(github)
    [outcome] = loop.run()
    assert github.writes == [("merge", "app", 1, "s1")]
    assert (outcome.action, fixer.calls) == ("merge", [])
    assert lines.of("renovate_repair_decision")[0]["update_type"] == "patch"


def test_a_minor_with_passing_checks_is_merged_too():
    github = fixture_repo(make_pull(labels=("minor",)), OK)
    build(github)[0].run()
    assert github.writes[0][0] == "merge"


def test_a_patch_with_a_failing_check_is_fixed_by_the_first_tier_that_succeeds_then_merged():
    github = fixture_repo(make_pull(), BAD)
    steps = [(False, "", []), (True, "s2", OK)]
    loop, fixer, lines, _ = build(github, steps)
    [outcome] = loop.run()
    assert [tier for tier, _ in fixer.calls] == ["tier-free", "tier-vibe"]
    assert github.writes == [("merge", "app", 1, "s2")]  # the merge is of the fixed head
    assert outcome.action == "fix-and-merge"
    assert outcome.attempts == ["tier-free: no fix pushed", "tier-vibe: fixed"]
    attempts = lines.of("renovate_repair_attempt")
    assert [(a["tier"], a["attempt"], a["outcome"], a["model"]) for a in attempts] == [
        ("tier-free", 1, "no-fix", None),
        ("tier-vibe", 2, "fixed", "model-of-tier-vibe"),
    ]
    assert all(a["repo"] == "JorisJonkers-dev/app" and a["pr"] == 1 for a in attempts)


def test_a_fix_that_still_fails_moves_on_to_the_next_tier():
    github = fixture_repo(make_pull(), BAD)
    steps = [(True, "s2", BAD), (True, "s3", BAD), (True, "s4", OK)]
    loop, fixer, lines, _ = build(github, steps)
    loop.run()
    assert [tier for tier, _ in fixer.calls] == ["tier-free", "tier-vibe", "tier-go-cheap"]
    assert [sha for _, sha in fixer.calls] == ["s1", "s2", "s3"]  # each starts from the previous head
    assert [a["outcome"] for a in lines.of("renovate_repair_attempt")] == ["checks-failed", "checks-failed", "fixed"]
    assert github.writes == [("merge", "app", 1, "s4")]


def test_when_no_tier_fixes_it_the_loop_comments_what_was_tried_and_stops():
    github = fixture_repo(make_pull(), BAD)
    steps = [(False, "", []), (True, "s2", BAD), (False, "", [])]
    loop, fixer, lines, _ = build(github, steps)
    [outcome] = loop.run()
    [(kind, _, _, body)] = github.writes
    assert kind == "comment"
    assert exhausted_marker("s2") in body
    assert all(tier in body for tier in ("tier-free", "tier-vibe", "tier-go-cheap"))
    assert "checks failing" in body
    assert outcome.action == "give-up"
    assert len(fixer.calls) == 3
    assert [a["outcome"] for a in lines.of("renovate_repair_attempt")] == ["no-fix", "checks-failed", "no-fix"]


def test_it_never_tries_a_tier_outside_the_three_and_honours_max_attempts():
    github = fixture_repo(make_pull(), BAD)
    steps = [(False, "", [])] * 3
    loop, fixer, _, _ = build(github, steps, max_attempts=1)
    loop.run()
    assert [tier for tier, _ in fixer.calls] == ["tier-free"]


def test_the_comment_never_carries_the_tool_output_but_the_log_does():
    github = fixture_repo(make_pull(), BAD)
    loop, _, lines, _ = build(github, [(False, "", [])] * 3)
    loop.run()
    assert "scripted" not in github.writes[0][3]
    assert {a["detail"] for a in lines.of("renovate_repair_attempt")} == {"scripted"}


def test_a_run_that_already_gave_up_on_a_commit_does_not_try_again():
    github = fixture_repo(make_pull(), BAD)
    github.comments[("app", 1)] = [exhausted_marker("s1")]
    loop, fixer, _, _ = build(github, [])
    [outcome] = loop.run()
    assert (outcome.action, fixer.calls, github.writes) == ("skip", [], [])


def test_checks_that_never_finish_after_a_fix_stop_the_loop_with_a_comment():
    github = fixture_repo(make_pull(), BAD)
    steps = [(True, "s2", RUNNING)]
    loop, fixer, lines, clock = build(github, steps, wait_s=100, poll_s=40)
    [outcome] = loop.run()
    assert len(fixer.calls) == 1
    assert clock.sleeps == [40, 40, 40]
    assert outcome.action == "give-up"
    assert lines.of("renovate_repair_attempt")[0]["outcome"] == "checks-timeout"
    assert github.writes[0][0] == "comment"


def test_checks_that_finish_while_waiting_are_picked_up():
    github = fixture_repo(make_pull(), BAD)
    steps = [(True, "s2", RUNNING)]
    loop, _, _, clock = build(github, steps)
    original = github.checks
    calls = []

    def checks(repo, sha):
        calls.append(sha)
        if sha == "s2" and len(calls) > 3:
            github.checks_by_sha["s2"] = OK
        return original(repo, sha)

    github.checks = checks
    [outcome] = loop.run()
    assert outcome.action == "fix-and-merge"
    assert clock.sleeps


def test_a_major_is_commented_once_and_never_merged_even_with_passing_checks():
    github = fixture_repo(make_pull(title="Update dependency foo to v2", labels=("major",)), OK)
    loop, fixer, _, _ = build(github)
    [first] = loop.run()
    [second] = loop.run()
    assert [w[0] for w in github.writes] == ["comment"]
    assert MAJOR_MARKER in github.writes[0][3]
    assert (first.action, second.action, fixer.calls) == ("comment-major", "skip", [])


def test_a_major_with_failing_checks_is_not_repaired():
    github = fixture_repo(make_pull(labels=("major",)), BAD)
    loop, fixer, _, _ = build(github, [(True, "s2", OK)])
    loop.run()
    assert fixer.calls == []


def test_an_update_with_no_readable_type_is_treated_as_a_major():
    github = fixture_repo(make_pull(title="Update foo", labels=()), OK)
    [outcome] = build(github)[0].run()
    assert (outcome.action, [w[0] for w in github.writes]) == ("comment-major", ["comment"])


@pytest.mark.parametrize(("checks", "state"), [(RUNNING, "pending"), ([], "none")])
def test_an_update_whose_checks_are_not_decided_is_left_for_the_next_run(checks, state):
    github = fixture_repo(make_pull(), checks)
    loop, fixer, _, _ = build(github)
    [outcome] = loop.run()
    assert (outcome.action, github.writes, fixer.calls) == ("wait", [], [])
    assert state in outcome.detail


def test_a_private_repository_is_never_read_or_touched():
    private = make_repo("secret", private=True, visibility="private")
    odd = make_repo("odd", private=False, visibility="internal")
    archived = make_repo("old", archived=True)
    public = make_repo("app")
    github = FakeGitHub(
        repos=[private, odd, archived, public],
        pulls={name: [make_pull()] for name in ("secret", "odd", "old", "app")},
        checks_by_sha={"s1": OK},
    )
    outcomes = build(github)[0].run()
    assert github.reads == ["JorisJonkers-dev/app"]
    assert [(o.repo, o.action) for o in outcomes] == [("JorisJonkers-dev/app", "merge")]
    assert {w[1] for w in github.writes} == {"app"}


def test_only_renovate_pull_requests_are_considered():
    github = fixture_repo(make_pull(author="someone"), OK)
    assert build(github)[0].run() == []
    assert github.writes == []


def test_repo_filter_limits_the_sweep():
    github = FakeGitHub(
        repos=[make_repo("a"), make_repo("b")],
        pulls={"a": [make_pull()], "b": [make_pull()]},
        checks_by_sha={"s1": OK},
    )
    build(github, only_repos=("b",))[0].run()
    assert github.reads == ["JorisJonkers-dev/b"]


def test_dry_run_writes_nothing_and_runs_no_fixer_and_prints_the_plan():
    repo = make_repo()
    github = FakeGitHub(
        repos=[repo],
        pulls={
            "app": [
                make_pull(1),
                make_pull(2, labels=("major",), sha="s2"),
                make_pull(3, sha="s3"),
                make_pull(4, sha="s4"),
            ]
        },
        checks_by_sha={"s1": OK, "s2": OK, "s3": BAD, "s4": RUNNING},
    )
    loop, fixer, lines, _ = build(github, dry_run=True)
    outcomes = loop.run()
    assert [o.action for o in outcomes] == ["merge", "comment-major", "repair", "wait"]
    assert (github.writes, fixer.calls) == ([], [])
    assert lines.of("renovate_repair_decision") == []
    plan = lines.of("renovate_repair_plan")
    assert [p["action"] for p in plan] == ["merge", "comment-major", "repair", "wait"]
    assert "tier-free, tier-vibe, tier-go-cheap" in plan[2]["detail"]


def test_a_github_error_on_one_pull_request_does_not_stop_the_others():
    github = FakeGitHub(
        repos=[make_repo()],
        pulls={"app": [make_pull(1), make_pull(2, sha="s2")]},
        checks_by_sha={"s1": OK, "s2": OK},
    )
    github.fail_merge = True
    loop, _, lines, _ = build(github)
    outcomes = loop.run()
    assert [o.action for o in outcomes] == ["error", "error"]
    assert "405" in outcomes[0].detail
    assert [d["action"] for d in lines.of("renovate_repair_decision")] == ["error", "error"]


def test_a_repository_whose_pulls_cannot_be_listed_is_reported_and_skipped():
    github = FakeGitHub(
        repos=[make_repo("bad"), make_repo("app")], pulls={"app": [make_pull()]}, checks_by_sha={"s1": OK}
    )
    github.fail_listing = {"bad"}
    outcomes = build(github)[0].run()
    assert [(o.repo, o.action) for o in outcomes] == [
        ("JorisJonkers-dev/bad", "error"),
        ("JorisJonkers-dev/app", "merge"),
    ]


@pytest.mark.parametrize(
    ("checks", "state"),
    [
        ([], "none"),
        ([Check("a", "success"), Check("b", "pending")], "pending"),
        ([Check("a", "success"), Check("b", "failure"), Check("c", "pending")], "failing"),
        ([Check("a", "success")], "passing"),
    ],
)
def test_checks_add_up(checks, state):
    assert aggregate(checks) == state


def test_a_draft_is_left_alone():
    draft = make_pull()
    draft = type(draft)(**{**draft.__dict__, "draft": True})
    github = fixture_repo(draft, OK)
    [outcome] = build(github)[0].run()
    assert (outcome.action, github.writes) == ("wait", [])


def test_a_run_repairs_at_most_max_repairs_pull_requests():
    github = FakeGitHub(
        repos=[make_repo()],
        pulls={"app": [make_pull(n, sha=f"s{n}") for n in (1, 2, 3)]},
        checks_by_sha={"s1": BAD, "s2": BAD, "s3": BAD},
    )
    loop, fixer, _, _ = build(github, [(False, "", [])] * 9, max_repairs=2)
    outcomes = loop.run()
    assert [o.action for o in outcomes] == ["give-up", "give-up", "wait"]
    assert "budget" in outcomes[2].detail
    assert len(fixer.calls) == 6
