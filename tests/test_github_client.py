"""GitHub client tests with a mocked HTTP session (no network)."""

from __future__ import annotations

import base64
import json

import pytest
import requests

from app.github.github_client import (
    GitHubAuthError,
    GitHubClient,
    GitHubConflictError,
    GitHubError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    parse_pr_url,
)


def make_response(status=200, payload=None, headers=None, links=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload if payload is not None else {}).encode()
    response.headers.update(headers or {})
    if links:
        response.headers["Link"] = ", ".join(
            f'<{url}>; rel="{rel}"' for rel, url in links.items()
        )
    return response


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_with(*responses):
    session = FakeSession(responses)
    return GitHubClient("tok", "https://api.test", session=session), session


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/o/r/pull/3") == ("o", "r", 3)
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/o/r/issues/3")


def test_token_sent_as_bearer_header():
    _, session = client_with()
    assert session.headers["Authorization"] == "Bearer tok"


def test_pagination_follows_next_links():
    client, session = client_with(
        make_response(
            payload=[{"filename": "a.py"}], links={"next": "https://api.test/p2"}
        ),
        make_response(payload=[{"filename": "b.py"}]),
    )
    files = client.get_pull_request_files("o", "r", 1)
    assert [f["filename"] for f in files] == ["a.py", "b.py"]
    assert session.requests[1][1] == "https://api.test/p2"


def test_get_file_content_decodes_base64():
    encoded = base64.b64encode(b"x = 1\n").decode()
    client, _ = client_with(
        make_response(
            payload={
                "type": "file",
                "encoding": "base64",
                "content": encoded,
                "sha": "abc",
            }
        )
    )
    assert client.get_file_content("o", "r", "a.py", "main") == ("x = 1\n", "abc")


def test_update_file_sends_base64_content_and_branch():
    client, session = client_with(make_response(payload={"commit": {"sha": "c1"}}))
    client.update_file("o", "r", "a.py", "x = 1\n", "msg", "feature", "oldsha")
    method, url, kwargs = session.requests[0]
    assert method == "PUT" and url.endswith("/repos/o/r/contents/a.py")
    body = kwargs["json"]
    assert base64.b64decode(body["content"]) == b"x = 1\n"
    assert body["branch"] == "feature" and body["sha"] == "oldsha"


@pytest.mark.parametrize(
    "status, headers, body, expected",
    [
        (401, {}, {"message": "Bad credentials"}, GitHubAuthError),
        (403, {}, {"message": "Resource not accessible"}, GitHubPermissionError),
        (
            403,
            {"X-RateLimit-Remaining": "0"},
            {"message": "forbidden"},
            GitHubRateLimitError,
        ),
        (429, {}, {"message": "slow down"}, GitHubRateLimitError),
        (404, {}, {"message": "Not Found"}, GitHubNotFoundError),
        (409, {}, {"message": "sha does not match"}, GitHubConflictError),
        (500, {}, {"message": "boom"}, GitHubError),
    ],
)
def test_http_errors_mapped(status, headers, body, expected):
    client, _ = client_with(make_response(status, body, headers))
    with pytest.raises(expected) as info:
        client.get_repository("o", "r")
    assert type(info.value) is expected


def test_network_failure_wrapped():
    client, _ = client_with(requests.ConnectionError("down"))
    with pytest.raises(GitHubError):
        client.get_repository("o", "r")


def test_directory_is_not_a_file():
    client, _ = client_with(make_response(payload=[{"name": "x"}]))
    with pytest.raises(GitHubNotFoundError):
        client.get_file_content("o", "r", "dir", "main")
