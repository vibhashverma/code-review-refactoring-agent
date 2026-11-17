"""Shared fakes and fixtures. No real GitHub or LLM access is used."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.checkpointer import create_checkpointer
from app.agents.graph import build_graph
from app.agents.nodes import Dependencies
from app.github.github_client import GitHubNotFoundError
from app.services.review_service import ReviewService

PR_URL = "https://github.com/octo/demo/pull/7"
ORIGINAL = (
    "def squares(nums):\n  result=[]\n  for n in nums:\n"
    "    result.append(n*n)\n  return result\n"
)
LOOP_VERSION = (
    "def squares(nums):\n    result = []\n    for n in nums:\n"
    "        result.append(n * n)\n    return result\n"
)
COMPREHENSION_VERSION = "def squares(nums):\n    return [n * n for n in nums]\n"


class FakeGitHub:
    """Records calls and serves a canned pull request."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fail_on = fail_on

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise GitHubNotFoundError(f"{name} missing", 404)

    def get_pull_request(self, owner, repo, number):
        self.calls.append(("get_pull_request", (owner, repo, number)))
        self._maybe_fail("get_pull_request")
        return {
            "head": {"ref": "feature", "repo": {"full_name": "octo/demo"}},
            "base": {"ref": "main"},
        }

    def get_pull_request_files(self, owner, repo, number):
        self.calls.append(("get_pull_request_files", (owner, repo, number)))
        return [
            {"filename": "app.py", "status": "modified"},
            {"filename": "README.md", "status": "modified"},
            {"filename": "old.py", "status": "removed"},
        ]

    def get_file_content(self, owner, repo, path, ref):
        self.calls.append(("get_file_content", (owner, repo, path, ref)))
        return ORIGINAL, "sha-original"

    def update_file(self, owner, repo, path, content, message, branch, sha):
        args = (owner, repo, path, content, message, branch, sha)
        self.calls.append(("update_file", args))
        self._maybe_fail("update_file")
        return {"commit": {"sha": "commit-123"}}

    def called(self, name: str) -> list[tuple]:
        return [args for n, args in self.calls if n == name]


class FakeLLM:
    """Returns a loop version first, a comprehension when feedback asks for it."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, messages: list[Any]) -> Any:
        prompt = messages[-1].content
        self.prompts.append(prompt)
        if "list comprehensions" in prompt:
            return SimpleNamespace(content=f"```python\n{COMPREHENSION_VERSION}```")
        return SimpleNamespace(content=LOOP_VERSION)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "checkpoints.sqlite")


@pytest.fixture
def make_service(db_path, github, llm):
    """Factory so tests can build several services on one SQLite file."""

    def _make() -> ReviewService:
        deps = Dependencies(github_factory=lambda: github, llm_factory=lambda: llm)
        graph = build_graph(create_checkpointer(db_path), deps)
        return ReviewService(graph)

    return _make


@pytest.fixture
def service(make_service) -> ReviewService:
    return make_service()
