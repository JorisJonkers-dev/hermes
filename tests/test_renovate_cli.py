"""The command line: arguments, the token file, and the exit status."""

import pytest

from loops.renovate_repair import cli
from loops.renovate_repair.models import Outcome


def test_defaults():
    args = cli.parse_args([])
    assert (args.org, args.repo, args.dry_run, args.max_attempts, args.work_dir) == (
        "JorisJonkers-dev",
        [],
        False,
        3,
        "/work",
    )


def test_a_missing_token_file_exits_2_before_anything_runs(capsys):
    assert cli.main(["--dry-run"], {}) == 2
    assert "GITHUB_TOKEN_FILE" in capsys.readouterr().err


@pytest.mark.parametrize(("actions", "status"), [(["merge", "wait"], 0), (["merge", "error"], 1)])
def test_the_loop_runs_with_the_settings_from_the_arguments_and_the_status_follows_errors(
    monkeypatch, tmp_path, actions, status
):
    token = tmp_path / "token"
    token.write_text("tok")
    seen = {}

    class Loop:
        def __init__(self, github, fixer, log, settings):
            seen.update(settings=settings, github=github, fixer=fixer)

        def run(self):
            return [Outcome("o/a", i, action) for i, action in enumerate(actions)]

    monkeypatch.setattr(cli, "RepairLoop", Loop)
    argv = [
        "--dry-run",
        "--org",
        "o",
        "--repo",
        "a",
        "--repo",
        "b",
        "--max-attempts",
        "2",
        "--wait-seconds",
        "9",
        "--work-dir",
        str(tmp_path),
    ]
    assert cli.main(argv, {"GITHUB_TOKEN_FILE": str(token)}) == status
    settings = seen["settings"]
    assert (settings.org, settings.only_repos, settings.dry_run, settings.max_attempts, settings.wait_s) == (
        "o",
        ("a", "b"),
        True,
        2,
        9,
    )


def test_main_reads_the_process_arguments_and_environment_by_default(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["x", "--dry-run"])
    monkeypatch.delenv("GITHUB_TOKEN_FILE", raising=False)
    assert cli.main() == 2


@pytest.mark.parametrize("flag", ["--max-attempts", "--max-repairs"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_counts_below_one_are_refused(flag, value):
    with pytest.raises(SystemExit):
        cli.parse_args([flag, value])


def test_a_failed_repository_listing_exits_1_with_a_message(monkeypatch, tmp_path, capsys):
    token = tmp_path / "token"
    token.write_text("tok")

    class Loop:
        def __init__(self, *args):
            pass

        def run(self):
            raise cli.GitHubError("GET /orgs/o/repos failed: OSError")

    monkeypatch.setattr(cli, "RepairLoop", Loop)
    assert cli.main(["--work-dir", str(tmp_path)], {"GITHUB_TOKEN_FILE": str(token)}) == 1
    assert "OSError" in capsys.readouterr().err
