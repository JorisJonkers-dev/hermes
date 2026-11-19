"""``python -m loops.renovate_repair``: parse the arguments, build the real client and fixer, run."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .fixer import HermesFixer
from .github import GitHubError, RestGitHub, read_token
from .loop import RepairLoop, Settings
from .routing import RoutingLog

DEFAULT_ORG = "JorisJonkers-dev"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m loops.renovate_repair", description=__doc__)
    parser.add_argument("--org", default=DEFAULT_ORG, help="the organisation whose public repositories are swept")
    parser.add_argument("--repo", action="append", default=[], help="only this repository (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="write nothing to GitHub; print what would be done")
    parser.add_argument("--max-attempts", type=int, default=3, help="repair attempts per pull request (tiers in order)")
    parser.add_argument("--work-dir", default="/work", help="where the throwaway clones are made")
    parser.add_argument("--wait-seconds", type=float, default=1800, help="how long to wait for checks after a fix")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    try:
        github = RestGitHub(read_token(env))
    except GitHubError as error:
        print(f"renovate-repair: {error}", file=sys.stderr)
        return 2
    settings = Settings(
        org=args.org,
        only_repos=tuple(args.repo),
        dry_run=args.dry_run,
        max_attempts=args.max_attempts,
        wait_s=args.wait_seconds,
    )
    fixer = HermesFixer(env, Path(args.work_dir))
    outcomes = RepairLoop(github, fixer, RoutingLog(), settings).run()
    return 1 if any(o.action == "error" for o in outcomes) else 0
