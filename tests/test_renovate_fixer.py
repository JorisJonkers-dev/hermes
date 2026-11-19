"""The Hermes fixer: clone, run the subagent on a tier's provider without the token, push through the helper."""

import pytest
from renovate_fakes import BAD, make_pull, make_repo

from loops.renovate_repair import fixer as fx
from loops.renovate_repair.models import Check

TOKEN_ENV = {"GITHUB_TOKEN_FILE": "/run/secrets/gh", "HERMES_HOME": "/home/hermes/.hermes"}


class Script:
    """A runner that answers by command and records ``(argv, cwd, env)``."""

    def __init__(self, hermes=None, status=" M src/a.py\n", fail=()):
        self.calls = []
        self.hermes = hermes or fx.Run(0, '{"event":"serving_model","litellm_model_id":"free/hy4"}\n')
        self.status = status
        self.fail = set(fail)

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append((list(argv), cwd, dict(env)))
        name = (
            argv[0]
            if argv[0] != "git"
            else next(a for a in argv[1:] if a in {"clone", "status", "add", "commit", "push"})
        )
        if name in self.fail:
            return fx.Run(1, "", f"{name} broke")
        if name == "hermes":
            return self.hermes
        if name == "status":
            return fx.Run(0, self.status)
        return fx.Run(0)

    def by(self, name):
        return [c for c in self.calls if name in c[0]]


def fixer_with(script, tmp_path):
    return fx.HermesFixer(TOKEN_ENV, tmp_path / "work", runner=script)


def pull():
    return make_pull(sha="s1")


def test_a_fix_is_made_on_the_tier_provider_committed_and_pushed(tmp_path):
    script = Script()
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-vibe", [Check("ci", "failure", "boom")])
    assert result == fx.FixResult(True, "pushed a fix to the Renovate branch", "free/hy4")
    argv, cwd, env = script.by("hermes")[0]
    assert argv[:3] == ["hermes", "chat", "-q"]
    assert argv[argv.index("--provider") + 1] == "litellm-tier-vibe"
    assert argv[argv.index("-m") + 1] == "tier-vibe"
    assert "boom" in argv[3]
    assert "renovate/pr-1" in argv[3]
    assert cwd.name == "clone"
    clone = script.by("clone")[0][0]
    assert clone[-1].endswith("/clone")
    assert "https://github.com/JorisJonkers-dev/app.git" in clone
    assert clone[clone.index("--branch") + 1] == "renovate/pr-1"


def test_the_subagent_and_every_step_but_the_push_never_see_the_token_file(tmp_path):
    script = Script()
    fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    with_token = [argv for argv, _, env in script.calls if "GITHUB_TOKEN_FILE" in env]
    assert [a for a in with_token if "push" in a] == with_token
    assert len(with_token) == 1
    push = with_token[0]
    assert push[:3] == ["git", "-c", "credential.helper="]
    assert "$GITHUB_TOKEN_FILE" in push[4]
    assert "password=" in push[4]
    assert "/run/secrets/gh" not in " ".join(push)  # the path is not an argument either
    assert push[-3:] == ["push", "origin", "HEAD:refs/heads/renovate/pr-1"]
    commit = script.by("commit")[0]
    assert commit[2]["GIT_AUTHOR_NAME"] == "hermes"


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
    [("clone", "clone failed"), ("add", "git add failed"), ("commit", "git commit failed"), ("push", "push failed")],
)
def test_a_failing_git_step_is_a_failed_attempt_not_a_crash(tmp_path, fail, word):
    result = fixer_with(Script(fail=[fail]), tmp_path).fix(make_repo(), pull(), "tier-free", BAD)
    assert not result.pushed
    assert word in result.detail
    assert f"{fail} broke" in result.detail


def test_a_hermes_failure_keeps_the_model_that_was_seen(tmp_path):
    hermes = fx.Run(3, '{"event":"serving_model","litellm_model_id":"m1"}\n', "bad")
    result = fixer_with(Script(hermes=hermes), tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, result.model) == (False, "m1")
    assert "hermes exited 3" in result.detail


def test_no_change_is_not_a_fix(tmp_path):
    script = Script(status="")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, result.detail, script.by("commit")) == (False, "the subagent made no change", [])


def test_a_change_to_the_workflows_is_refused(tmp_path):
    script = Script(status=" M src/a.py\n?? .github/workflows/x.yml\n")
    result = fixer_with(script, tmp_path).fix(make_repo(), pull(), "tier-free", [])
    assert (result.pushed, script.by("commit")) == (False, [])
    assert ".github/" in result.detail


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


def test_the_subprocess_runner_runs_and_reports_a_timeout(tmp_path):
    ok = fx.subprocess_runner(
        ["python3", "-c", "import sys; print('hi'); sys.exit(4)"], tmp_path, {"PATH": "/usr/bin:/bin"}, 30
    )
    assert (ok.code, ok.stdout.strip()) == (4, "hi")
    slow = fx.subprocess_runner(["sleep", "5"], None, {"PATH": "/usr/bin:/bin"}, 0.05)
    assert slow.code == 124
    assert "timed out" in slow.stderr


def test_a_git_credential_helper_hands_git_the_token_from_the_file(tmp_path):
    import subprocess

    token = tmp_path / "t"
    token.write_text("abc123")
    helper = fx.CREDENTIAL_HELPER.removeprefix("!")
    out = subprocess.run(
        ["sh", "-c", helper],
        env={"GITHUB_TOKEN_FILE": str(token), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split() == ["username=x-access-token", "password=abc123"]
