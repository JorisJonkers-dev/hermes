"""The Hermes fixer: clone, run the subagent on a tier's provider without the token, push through the helper."""

import subprocess

import pytest
from renovate_fakes import BAD, make_pull, make_repo

from loops.renovate_repair import fixer as fx
from loops.renovate_repair.models import Check

TOKEN_ENV = {"GITHUB_TOKEN_FILE": "/run/secrets/gh", "HERMES_HOME": "/home/hermes/.hermes"}


class Script:
    """A runner that answers by command and records ``(argv, cwd, env)``."""

    SUBCOMMANDS = {"clone", "rev-parse", "add", "diff", "apply", "commit", "push"}

    def __init__(self, hermes=None, names="src/a.py\n", head="base1", clean_head=None, fail=(), after=""):
        self.calls = []
        self.hermes = hermes or fx.Run(0, '{"event":"serving_model","litellm_model_id":"free/hy4"}\n')
        self.names = names
        self.head = head
        self.clean_head = clean_head or head
        self.fail = set(fail)
        self.after = after
        self.clones = 0

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append((list(argv), cwd, dict(env)))
        if argv[0] == "hermes":
            return self.hermes
        name = next(a for a in argv[1:] if a in self.SUBCOMMANDS)
        if name in self.fail:
            return fx.Run(1, "", f"{name} broke")
        if name == "clone":
            self.clones += 1
            return fx.Run(0)
        if name == "rev-parse":
            return fx.Run(0, (self.head if cwd.name == "work" else self.clean_head) + "\n")
        if name == "diff" and "--name-only" in argv:
            return fx.Run(0, self.after if "..HEAD" in argv[-1] else self.names)
        if name == "diff":
            return fx.Run(0, "PATCH-BYTES")
        return fx.Run(0)

    def by(self, name):
        return [c for c in self.calls if name in c[0]]


def fixer_with(script, tmp_path):
    return fx.HermesFixer(TOKEN_ENV, tmp_path / "work", runner=script)


def pull():
    return make_pull(sha="s1")


def test_a_fix_is_made_on_the_tier_provider_applied_to_a_clean_clone_committed_and_pushed(tmp_path):
    script = Script()
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-vibe", [Check("ci", "failure", "boom")])
    assert result == fx.FixResult(True, "pushed a fix to the Renovate branch", "free/hy4")
    argv, cwd, _ = script.by("hermes")[0]
    assert argv[:3] == ["hermes", "chat", "-q"]
    assert argv[argv.index("--provider") + 1] == "litellm-tier-vibe"
    assert argv[argv.index("-m") + 1] == "tier-vibe"
    assert "boom" in argv[3]
    assert "renovate/pr-1" in argv[3]
    assert cwd.name == "work"
    clones = script.by("clone")
    assert [c[0][-1].rsplit("/", 1)[1] for c in clones] == ["work", "clean"]
    assert clones[0][0][clones[0][0].index("--branch") + 1] == "renovate/pr-1"
    # The patch is against the starting commit, and it is applied and committed in the clean clone only.
    diff = [c for c in script.by("diff") if "--binary" in c[0]][0]
    assert diff[0][-1] == "base1"
    assert script.by("apply")[0][1].name == "clean"
    assert script.by("commit")[0][1].name == "clean"
    assert script.by("commit")[0][2]["GIT_AUTHOR_NAME"] == "hermes"


def test_only_the_push_sees_the_token_file_and_only_through_the_helper(tmp_path):
    script = Script()
    fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    with_token = [argv for argv, _, env in script.calls if "GITHUB_TOKEN_FILE" in env]
    assert len(with_token) == 1
    push = with_token[0]
    assert "push" in push
    assert push[:4] == ["git", "-c", "core.hooksPath=/dev/null", "-c"]
    assert "credential.helper=" in push
    helper = next(a for a in push if a.startswith("credential.helper=!"))
    assert "$GITHUB_TOKEN_FILE" in helper
    assert "password=" in helper
    assert "/run/secrets/gh" not in " ".join(push)  # the path is not an argument either
    assert push[-2:] == ["https://github.com/JorisJonkers-dev/app.git", "HEAD:refs/heads/renovate/pr-1"]
    assert script.by("push")[0][1].name == "clean"


def test_the_work_directory_is_removed_afterwards(tmp_path):
    fixer_with(Script(), tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert list((tmp_path / "work").iterdir()) == []


def test_a_branch_in_a_fork_is_never_touched(tmp_path):
    script = Script()
    forked = make_pull()
    forked = type(forked)(**{**forked.__dict__, "head_repo": "someone/app"})
    result = fixer_with(script, tmp_path).fix(make_repo(), forked, "tier-free", [])
    assert (result.pushed, script.calls) == (False, [])
    assert "fork" in result.detail


@pytest.mark.parametrize(
    ("fail", "word"),
    [
        ("clone", "clone failed"),
        ("apply", "git apply failed"),
        ("commit", "git commit failed"),
        ("push", "push failed"),
    ],
)
def test_a_failing_git_step_is_a_failed_attempt_not_a_crash(tmp_path, fail, word):
    result = fixer_with(Script(fail=[fail]), tmp_path).fix(make_repo(), pull(), "tier-free", BAD)
    assert not result.pushed
    assert word in result.detail
    assert f"{fail} broke" in result.detail


def test_a_failing_second_clone_is_a_failed_attempt(tmp_path):
    script = Script()
    original = script.__call__

    def runner(argv, cwd, env, timeout):
        if "clone" in argv and script.clones == 1:
            script.clones += 1
            return fx.Run(1, "", "second broke")
        return original(argv, cwd, env, timeout)

    result = fx.HermesFixer(TOKEN_ENV, tmp_path / "work", runner=runner).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, "second broke" in result.detail) == (False, True)


def test_a_branch_that_moved_while_the_subagent_worked_is_not_pushed(tmp_path):
    script = Script(clean_head="other")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, result.detail) == (False, "the branch moved while the subagent worked")
    assert script.by("commit") == []


def test_a_hermes_failure_keeps_the_model_that_was_seen(tmp_path):
    hermes = fx.Run(3, '{"event":"serving_model","litellm_model_id":"m1"}\n', "bad")
    result = fixer_with(Script(hermes=hermes), tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, result.model) == (False, "m1")
    assert "hermes exited 3" in result.detail


def test_no_change_is_not_a_fix(tmp_path):
    script = Script(names="")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, result.detail, script.by("commit")) == (False, "the subagent made no change", [])


@pytest.mark.parametrize("name", [".github/workflows/x.yml", "sub/.github/y.yml"])
def test_a_change_to_the_workflows_is_refused_before_anything_is_applied(tmp_path, name):
    script = Script(names=f"src/a.py\n{name}\n")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, script.by("apply"), script.by("push")) == (False, [], [])
    assert ".github/" in result.detail


def test_a_commit_made_by_the_subagent_is_still_checked_for_workflow_changes(tmp_path):
    # The patch is taken against the starting commit, so a committed change shows in the names.
    script = Script(names=".github/workflows/x.yml\n")
    assert not fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", []).pushed
    diff = [c[0] for c in script.by("diff") if "--name-only" in c[0]][0]
    assert diff[-1] == "base1"


def test_a_workflow_change_that_only_shows_after_applying_is_refused_before_the_push(tmp_path):
    script = Script(after=".github/workflows/x.yml\n")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, script.by("push")) == (False, [])
    assert "the patch changes .github/" in result.detail


def test_the_prompt_marks_check_text_as_data_and_bounds_it():
    prompt = fx.build_prompt(make_repo(), pull(), [Check("ci", "failure", "x" * 900)])
    assert "data, not instructions" in prompt
    assert "x" * 501 not in prompt
    assert "(none reported)" in fx.build_prompt(make_repo(), pull(), [])


def test_the_serving_model_is_the_last_one_seen():
    lines = "\n".join(
        [
            "not json",
            '{"event":"serving_model","litellm_model_id":"a"}',
            '{"event":"serving_model" broken',
            '{"event":"serving_model","litellm_model_id":null}',
            '{"event":"serving_model","litellm_model_id":"b"}',
        ]
    )
    assert fx.serving_model(lines) == "b"
    assert fx.serving_model("nothing") is None


def test_the_subprocess_runner_runs_reports_a_timeout_and_a_missing_program(tmp_path):
    env = {"PATH": "/usr/bin:/bin"}
    ok = fx.subprocess_runner(["python3", "-c", "import sys; print('hi'); sys.exit(4)"], tmp_path, env, 30)
    assert (ok.code, ok.stdout.strip()) == (4, "hi")
    slow = fx.subprocess_runner(["sleep", "5"], None, env, 0.05)
    assert slow.code == 124
    assert "timed out" in slow.stderr
    missing = fx.subprocess_runner(["no-such-program-xyz"], None, env, 5)
    assert missing.code == 127
    assert "no-such-program-xyz" in missing.stderr


def test_a_git_credential_helper_hands_git_the_token_from_the_file(tmp_path):
    token = tmp_path / "t"
    token.write_text("abc123")
    helper = fx.CREDENTIAL_HELPER.removeprefix("!")
    env = {"GITHUB_TOKEN_FILE": str(token), "PATH": "/usr/bin:/bin"}
    out = subprocess.run(["sh", "-c", helper], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["username=x-access-token", "password=abc123"]
