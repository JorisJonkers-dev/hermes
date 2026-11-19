"""The GitHub side of the loop: a small interface, and the REST client behind it.

The loop depends on :class:`GitHub` only, so a test hands it a fake. :class:`RestGitHub` is the real
one: stdlib ``urllib``, no dependency. The token is read from a file (never from an environment
variable that holds it) and goes into one request header; it is never logged or put in an error.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .models import Check, PullRequest, Repo

API = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGES = 10


class GitHubError(RuntimeError):
    """A GitHub call failed. The message holds the method, path and status, never the token."""


class GitHub(Protocol):
    def public_repos(self, org: str) -> list[Repo]: ...
    def open_pulls(self, repo: Repo) -> list[PullRequest]: ...
    def pull(self, repo: Repo, number: int) -> PullRequest: ...
    def checks(self, repo: Repo, sha: str) -> list[Check]: ...
    def has_comment(self, repo: Repo, number: int, marker: str) -> bool: ...
    def comment(self, repo: Repo, number: int, body: str) -> None: ...
    def merge(self, repo: Repo, number: int, sha: str) -> None: ...


def read_token(environ: Mapping[str, str], variable: str = "GITHUB_TOKEN_FILE") -> str:
    """The token in the file ``environ[variable]`` names. An empty file or a missing variable is an error."""
    path = environ.get(variable)
    if not path:
        raise GitHubError(f"{variable} is not set: it must name a file that holds the GitHub token")
    try:
        token = Path(path).read_text().strip()
    except OSError as error:
        raise GitHubError(f"cannot read the token file named by {variable}: {error.strerror}") from error
    if not token:
        raise GitHubError(f"the token file named by {variable} is empty")
    return token


#: ``(method, url, headers, body) -> (status, response body)``
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


def urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _repo(item: Mapping[str, Any]) -> Repo:
    return Repo(
        owner=item["owner"]["login"],
        name=item["name"],
        private=bool(item.get("private", True)),
        visibility=str(item.get("visibility", "")),
        archived=bool(item.get("archived", False)),
    )


def _pull(item: Mapping[str, Any]) -> PullRequest:
    head = item["head"]
    head_repo = head.get("repo") or {}
    return PullRequest(
        number=item["number"],
        title=item.get("title") or "",
        body=item.get("body") or "",
        labels=tuple(label["name"] for label in item.get("labels", [])),
        author=(item.get("user") or {}).get("login", ""),
        head_sha=head["sha"],
        head_ref=head["ref"],
        head_repo=head_repo.get("full_name", ""),
    )


_RUN_OK = {"success", "neutral", "skipped"}
_STATUS_OK = {"success"}
_STATUS_PENDING = {"pending"}


def _run_state(run: Mapping[str, Any]) -> str:
    if run.get("status") != "completed":
        return "pending"
    return "success" if run.get("conclusion") in _RUN_OK else "failure"


def _status_state(status: Mapping[str, Any]) -> str:
    state = status.get("state")
    if state in _STATUS_OK:
        return "success"
    return "pending" if state in _STATUS_PENDING else "failure"


class RestGitHub:
    def __init__(self, token: str, transport: Transport = urllib_transport, api: str = API) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hermes-renovate-repair",
        }
        self._transport = transport
        self._api = api

    def _call(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {**self._headers, **({"Content-Type": "application/json"} if body else {})}
        status, data = self._transport(method, self._api + path, headers, body)
        if status >= 400:
            raise GitHubError(f"{method} {path.split('?')[0]} failed with status {status}")
        return json.loads(data) if data else None

    def _pages(self, path: str, key: str | None = None) -> list[Any]:
        items: list[Any] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            payload = self._call("GET", f"{path}{separator}per_page={PAGE_SIZE}&page={page}")
            batch = payload[key] if key else payload
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return items

    def public_repos(self, org: str) -> list[Repo]:
        return [_repo(item) for item in self._pages(f"/orgs/{quote(org)}/repos?type=public")]

    def open_pulls(self, repo: Repo) -> list[PullRequest]:
        return [_pull(item) for item in self._pages(f"/repos/{repo.full_name}/pulls?state=open")]

    def pull(self, repo: Repo, number: int) -> PullRequest:
        return _pull(self._call("GET", f"/repos/{repo.full_name}/pulls/{number}"))

    def checks(self, repo: Repo, sha: str) -> list[Check]:
        base = f"/repos/{repo.full_name}/commits/{sha}"
        runs = self._pages(f"{base}/check-runs", key="check_runs")
        statuses = self._call("GET", f"{base}/status").get("statuses", [])
        found = [
            Check(run["name"], _run_state(run), ((run.get("output") or {}).get("summary") or "")[:2000]) for run in runs
        ]
        found += [Check(s["context"], _status_state(s), s.get("description") or "") for s in statuses]
        return found

    def has_comment(self, repo: Repo, number: int, marker: str) -> bool:
        comments = self._pages(f"/repos/{repo.full_name}/issues/{number}/comments")
        return any(marker in (comment.get("body") or "") for comment in comments)

    def comment(self, repo: Repo, number: int, body: str) -> None:
        self._call("POST", f"/repos/{repo.full_name}/issues/{number}/comments", {"body": body})

    def merge(self, repo: Repo, number: int, sha: str) -> None:
        self._call("PUT", f"/repos/{repo.full_name}/pulls/{number}/merge", {"merge_method": "squash", "sha": sha})
