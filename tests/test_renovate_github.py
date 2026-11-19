"""The REST client: token from a file, paging, mapping, and errors that never carry the token."""

import io
import json
import urllib.error

import pytest

from loops.renovate_repair import github as gh
from loops.renovate_repair.models import Check, Repo

PUBLIC = Repo("o", "app", False, "public")


def test_the_token_is_read_from_the_file_the_variable_names(tmp_path):
    path = tmp_path / "token"
    path.write_text("  s3cret\n")
    assert gh.read_token({"GITHUB_TOKEN_FILE": str(path)}) == "s3cret"


def test_a_missing_variable_an_unreadable_file_and_an_empty_file_are_errors(tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(gh.GitHubError, match="GITHUB_TOKEN_FILE is not set"):
        gh.read_token({})
    with pytest.raises(gh.GitHubError, match="cannot read the token file"):
        gh.read_token({"GITHUB_TOKEN_FILE": str(tmp_path / "nope")})
    with pytest.raises(gh.GitHubError, match="is empty"):
        gh.read_token({"GITHUB_TOKEN_FILE": str(empty)})


class Transport:
    """Serves canned JSON by ``METHOD path`` and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, dict(headers), body))
        key = f"{method} {url.removeprefix(gh.API)}"
        status, payload = self.routes[key] if key in self.routes else self.routes[key.split("?")[0]]
        return status, json.dumps(payload).encode() if payload is not None else b""


def repo_item(name, **extra):
    return {"name": name, "owner": {"login": "o"}, **extra}


def test_repos_come_from_the_public_listing_and_a_missing_private_flag_means_private():
    transport = Transport(
        {
            "GET /orgs/o/repos?type=public&per_page=100&page=1": (
                200,
                [repo_item("a", private=False, visibility="public"), repo_item("b"), repo_item("c", archived=True)],
            )
        }
    )
    repos = gh.RestGitHub("tok", transport).public_repos("o")
    assert [(r.name, r.private, r.visibility, r.archived, r.is_public) for r in repos] == [
        ("a", False, "public", False, True),
        ("b", True, "", False, False),
        ("c", True, "", True, False),
    ]


def test_listings_follow_pages_until_a_short_page():
    full = [{"number": i, "head": {"sha": "s", "ref": "r"}} for i in range(100)]
    transport = Transport(
        {
            "GET /repos/o/app/pulls?state=open&per_page=100&page=1": (200, full),
            "GET /repos/o/app/pulls?state=open&per_page=100&page=2": (200, full[:1]),
        }
    )
    pulls = gh.RestGitHub("tok", transport).open_pulls(PUBLIC)
    assert len(pulls) == 101


def test_a_pull_request_is_mapped_with_its_labels_author_and_branch():
    item = {
        "number": 7,
        "title": "t",
        "body": None,
        "labels": [{"name": "patch"}],
        "user": {"login": "renovate[bot]"},
        "head": {"sha": "abc", "ref": "renovate/x", "repo": {"full_name": "o/app"}},
    }
    bare = {"number": 8, "head": {"sha": "d", "ref": "r", "repo": None}, "user": None}
    transport = Transport({"GET /repos/o/app/pulls/7": (200, item), "GET /repos/o/app/pulls/8": (200, bare)})
    client = gh.RestGitHub("tok", transport)
    repo = PUBLIC
    pull = client.pull(repo, 7)
    assert (pull.number, pull.body, pull.labels, pull.author, pull.head_sha, pull.head_ref, pull.head_repo) == (
        7,
        "",
        ("patch",),
        "renovate[bot]",
        "abc",
        "renovate/x",
        "o/app",
    )
    assert (client.pull(repo, 8).author, client.pull(repo, 8).head_repo) == ("", "")


def test_checks_combine_check_runs_and_commit_statuses():
    runs = {
        "check_runs": [
            {"name": "ok", "status": "completed", "conclusion": "success", "output": {"summary": "s" * 3000}},
            {"name": "skip", "status": "completed", "conclusion": "skipped"},
            {"name": "bad", "status": "completed", "conclusion": "failure", "output": None},
            {"name": "run", "status": "in_progress", "conclusion": None},
        ]
    }
    statuses = {
        "statuses": [
            {"context": "s-ok", "state": "success", "description": None},
            {"context": "s-wait", "state": "pending", "description": "d"},
            {"context": "s-bad", "state": "error"},
        ]
    }
    transport = Transport(
        {
            "GET /repos/o/app/commits/sha/check-runs?per_page=100&page=1": (200, runs),
            "GET /repos/o/app/commits/sha/status": (200, statuses),
        }
    )
    checks = gh.RestGitHub("tok", transport).checks(PUBLIC, "sha")
    assert checks == [
        Check("ok", "success", "s" * 2000),
        Check("skip", "success", ""),
        Check("bad", "failure", ""),
        Check("run", "pending", ""),
        Check("s-ok", "success", ""),
        Check("s-wait", "pending", "d"),
        Check("s-bad", "failure", ""),
    ]


def test_writes_send_the_documented_bodies_and_the_token_only_in_the_header():
    transport = Transport(
        {
            "GET /repos/o/app/issues/3/comments?per_page=100&page=1": (200, [{"body": "x <!-- m -->"}, {"body": None}]),
            "POST /repos/o/app/issues/3/comments": (201, {}),
            "PUT /repos/o/app/pulls/3/merge": (200, {"merged": True}),
        }
    )
    client = gh.RestGitHub("tok", transport)
    repo = PUBLIC
    assert client.has_comment(repo, 3, "<!-- m -->") is True
    assert client.has_comment(repo, 3, "<!-- other -->") is False
    client.comment(repo, 3, "hello")
    client.merge(repo, 3, "abc")
    post = next(r for r in transport.requests if r[0] == "POST")
    put = next(r for r in transport.requests if r[0] == "PUT")
    assert json.loads(post[3]) == {"body": "hello"}
    assert json.loads(put[3]) == {"merge_method": "squash", "sha": "abc"}
    assert post[2]["Content-Type"] == "application/json"
    assert all(r[2]["Authorization"] == "Bearer tok" for r in transport.requests)
    assert not any("tok" in r[1] for r in transport.requests)


def test_an_error_status_raises_without_the_query_or_the_token():
    transport = Transport({"GET /repos/o/app/pulls/1": (403, {"message": "no"})})
    with pytest.raises(gh.GitHubError) as error:
        gh.RestGitHub("tok", transport).pull(PUBLIC, 1)
    assert str(error.value) == "GET /repos/o/app/pulls/1 failed with status 403"


def test_the_stdlib_transport_returns_status_and_body(monkeypatch):
    seen = {}

    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        seen["request"], seen["timeout"] = request, timeout
        return Response(b"{}")

    monkeypatch.setattr(gh.urllib.request, "urlopen", urlopen)
    assert gh.urllib_transport("POST", "https://x/y", {"A": "b"}, b"{}") == (200, b"{}")
    assert (seen["request"].get_method(), seen["request"].full_url, seen["timeout"]) == ("POST", "https://x/y", 30)


def test_the_stdlib_transport_turns_an_http_error_into_a_status(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr(gh.urllib.request, "urlopen", urlopen)
    assert gh.urllib_transport("GET", "https://x/y", {}, None) == (404, b"nope")


def test_paging_stops_at_the_page_cap_even_if_every_page_is_full():
    full = json.dumps([{"number": i, "head": {"sha": "s", "ref": "r"}} for i in range(100)]).encode()
    client = gh.RestGitHub("tok", lambda method, url, headers, body: (200, full))
    assert len(client.open_pulls(PUBLIC)) == 100 * gh.MAX_PAGES


@pytest.mark.parametrize("failure", [OSError("down"), TimeoutError(), urllib.error.URLError("dns")])
def test_a_network_failure_is_a_github_error_naming_only_the_call(failure):
    def transport(method, url, headers, body):
        raise failure

    with pytest.raises(gh.GitHubError) as error:
        gh.RestGitHub("tok", transport).pull(PUBLIC, 1)
    assert str(error.value) == f"GET /repos/o/app/pulls/1 failed: {type(failure).__name__}"


def test_a_body_that_is_not_json_is_a_github_error():
    with pytest.raises(gh.GitHubError, match="JSONDecodeError"):
        gh.RestGitHub("tok", lambda *a: (200, b"<html>")).pull(PUBLIC, 1)


def test_a_draft_flag_is_read():
    item = {"number": 1, "draft": True, "head": {"sha": "s", "ref": "r"}}
    client = gh.RestGitHub("tok", lambda *a: (200, json.dumps(item).encode()))
    assert client.pull(PUBLIC, 1).draft is True
