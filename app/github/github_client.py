"""Thin GitHub REST API client built on ``requests``."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

PR_URL_RE = re.compile(
    r"^https?://[^/]+/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?$"
)


class GitHubError(Exception):
    """Base error for GitHub API failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    """Invalid or missing credentials (HTTP 401)."""


class GitHubPermissionError(GitHubError):
    """Token lacks permission for the operation (HTTP 403)."""


class GitHubNotFoundError(GitHubError):
    """Repository, pull request or file not found (HTTP 404)."""


class GitHubRateLimitError(GitHubError):
    """API rate limit exceeded."""


class GitHubConflictError(GitHubError):
    """Update conflict, e.g. the file changed since it was fetched (409/422)."""


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Split a PR URL into ``(owner, repo, number)``."""
    match = PR_URL_RE.match(pr_url.strip())
    if not match:
        raise ValueError(f"Not a valid pull request URL: {pr_url!r}")
    return match["owner"], match["repo"], int(match["number"])


class GitHubClient:
    """Minimal GitHub REST client covering what the review agent needs."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "code-review-refactoring-agent",
            }
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "GitHubClient":
        """Build a client from application settings."""
        settings = settings or get_settings()
        return cls(
            settings.require_github_token(),
            settings.github_api_url,
            settings.http_timeout_seconds,
        )

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self._api_url}{path}"
        try:
            response = self._session.request(
                method, url, timeout=self._timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise GitHubError(f"GitHub request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise self._to_error(response)
        return response

    @staticmethod
    def _to_error(response: requests.Response) -> GitHubError:
        status = response.status_code
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = response.text[:200]
        headers = response.headers or {}
        rate_limited = (
            status == 429
            or headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in detail.lower()
        )
        if status in (403, 429) and rate_limited:
            return GitHubRateLimitError(f"GitHub rate limit exceeded: {detail}", status)
        if status == 401:
            return GitHubAuthError(f"Invalid GitHub token: {detail}", status)
        if status == 403:
            return GitHubPermissionError(f"Permission denied: {detail}", status)
        if status == 404:
            return GitHubNotFoundError(f"Not found: {detail}", status)
        if status in (409, 422):
            return GitHubConflictError(f"Conflict: {detail}", status)
        return GitHubError(f"GitHub API error {status}: {detail}", status)

    def _paginate(self, path: str, per_page: int = 100) -> list[dict[str, Any]]:
        """Follow ``Link: rel=next`` headers and return all items."""
        items: list[dict[str, Any]] = []
        url: str | None = path
        params: dict[str, Any] | None = {"per_page": per_page}
        while url:
            response = self._request("GET", url, params=params)
            items.extend(response.json())
            url = response.links.get("next", {}).get("url")
            params = None  # next URL already carries the query string
        return items

    # ------------------------------------------------------------------- API
    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """Return repository metadata."""
        return self._request("GET", f"/repos/{owner}/{repo}").json()

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Return pull request details (includes head/base branch info)."""
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}").json()

    def get_pull_request_files(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        """Return every file modified by the PR (paginated)."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/files")

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[str, str]:
        """Return ``(decoded_text, blob_sha)`` for a file at ``ref``."""
        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{quote(path)}",
            params={"ref": ref},
        ).json()
        if isinstance(data, list) or data.get("type") != "file":
            raise GitHubNotFoundError(f"{path} is not a file", 404)
        if data.get("encoding") != "base64":
            raise GitHubError(f"Unsupported encoding for {path}")
        raw = base64.b64decode(data["content"])
        try:
            return raw.decode("utf-8"), data["sha"]
        except UnicodeDecodeError as exc:
            raise GitHubError(f"{path} is not valid UTF-8 text") from exc

    def update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str,
    ) -> dict[str, Any]:
        """Commit new file content to ``branch``; returns the API response."""
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": branch,
        }
        return self._request(
            "PUT", f"/repos/{owner}/{repo}/contents/{quote(path)}", json=body
        ).json()
