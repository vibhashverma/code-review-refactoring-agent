"""LangGraph node implementations.

Nodes receive their external dependencies (GitHub client, LLM) through a
``Dependencies`` object so they can be swapped for fakes in tests.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.prompts import (
    FEEDBACK_SECTION,
    PREVIOUS_SECTION,
    SYSTEM_PROMPT,
    USER_PROMPT,
)
from app.agents.state import ReviewState
from app.config import get_settings
from app.github.github_client import GitHubClient, GitHubError, parse_pr_url
from app.tools.linter_tool import run_linter_tool

logger = logging.getLogger(__name__)

FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+-]*\n(?P<body>.*?)\n?```\s*$", re.DOTALL)


@dataclass
class Dependencies:
    """Lazily constructed external services used by the nodes."""

    github_factory: Callable[[], GitHubClient]
    llm_factory: Callable[[], Any]


def default_dependencies() -> Dependencies:
    """Production dependencies: real GitHub client and Gemini chat model."""

    def make_llm() -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI

        settings = get_settings()
        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.require_gemini_key(),
            temperature=0,
            timeout=60,
        )

    return Dependencies(github_factory=GitHubClient.from_settings, llm_factory=make_llm)


def _event(event: str, **details: Any) -> dict[str, Any]:
    return {
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **details,
    }


def _error(message: str, event: str) -> dict[str, Any]:
    logger.error("%s: %s", event, message)
    return {
        "execution_status": "error",
        "error_message": message,
        "history": [_event(event, error=message)],
    }


def _message_text(content: Any) -> str:
    """Flatten chat-model content (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)


def strip_code_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence if the model added one."""
    text = text.strip()
    match = FENCE_RE.match(text)
    body = match["body"] if match else text
    return body.rstrip("\n") + "\n"


# --------------------------------------------------------------------- nodes
def fetch_pr_node(state: ReviewState, deps: Dependencies) -> dict[str, Any]:
    """Fetch the PR, its modified Python files and their contents."""
    settings = get_settings()
    try:
        base_owner, base_repo, number = parse_pr_url(state["pr_url"])
        client = deps.github_factory()
        pr = client.get_pull_request(base_owner, base_repo, number)
        head = pr["head"]
        head_repo = head.get("repo")
        if not head_repo:
            return _error("PR head repository no longer exists", "fetch_pr_failed")
        owner, repo = head_repo["full_name"].split("/", 1)
        branch = head["ref"]

        files = client.get_pull_request_files(base_owner, base_repo, number)
        py_files = [
            f["filename"]
            for f in files
            if f["filename"].endswith(".py") and f.get("status") != "removed"
        ]
        if not py_files:
            return {
                "execution_status": "skipped",
                "error_message": "PR contains no modified Python files",
                "history": [_event("fetch_pr_skipped", reason="no python files")],
            }
        py_files = py_files[: settings.max_files_per_pr]

        contents: dict[str, str] = {}
        shas: dict[str, str] = {}
        for path in py_files:
            text, sha = client.get_file_content(owner, repo, path, branch)
            if len(text.encode("utf-8")) > settings.max_file_bytes:
                logger.warning("Skipping oversized file %s", path)
                continue
            contents[path], shas[path] = text, sha
        if not contents:
            return _error("All modified files exceed the size limit", "fetch_pr_failed")
    except (GitHubError, ValueError, KeyError) as exc:
        return _error(f"Failed to fetch PR: {exc}", "fetch_pr_failed")

    return {
        "owner": owner,
        "repository": repo,
        "branch": branch,
        "base_branch": pr["base"]["ref"],
        "pull_number": number,
        "file_paths": list(contents),
        "code_contents": contents,
        "file_shas": shas,
        "execution_status": "fetched",
        "error_message": "",
        "history": [
            _event("pr_fetched", files=list(contents), branch=branch, head=owner)
        ],
    }


def lint_code_node(state: ReviewState, deps: Dependencies) -> dict[str, Any]:
    """Run the sandboxed linter over every fetched file."""
    results: dict[str, dict[str, Any]] = {}
    for path, code in state["code_contents"].items():
        try:
            results[path] = json.loads(run_linter_tool.invoke({"code_string": code}))
        except Exception as exc:  # linter must never crash the graph
            results[path] = {"ok": False, "issues": [], "errors": [str(exc)]}
    total = sum(len(r.get("issues", [])) for r in results.values())
    return {
        "lint_errors": results,
        "execution_status": "linted",
        "history": [_event("code_linted", files=len(results), issues=total)],
    }


def propose_refactor_node(state: ReviewState, deps: Dependencies) -> dict[str, Any]:
    """Ask the LLM for refactored code, honouring any reviewer feedback."""
    feedback = (state.get("reviewer_feedback") or "").strip()
    previous = state.get("refactored_code") or {}
    revision = state.get("revision_count", 0) + 1
    try:
        llm = deps.llm_factory()
        proposals: dict[str, str] = {}
        for path, code in state["code_contents"].items():
            prompt = USER_PROMPT.format(
                path=path,
                code=code,
                lint=json.dumps(state.get("lint_errors", {}).get(path, {}), indent=2),
                previous_section=(
                    PREVIOUS_SECTION.format(previous=previous[path])
                    if feedback and path in previous
                    else ""
                ),
                feedback_section=(
                    FEEDBACK_SECTION.format(feedback=feedback) if feedback else ""
                ),
            )
            reply = llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
            )
            content = _message_text(reply.content)
            proposals[path] = strip_code_fences(content)
    except Exception as exc:  # LLM/network/config failures
        return _error(
            f"Refactoring failed: {type(exc).__name__}: {exc}", "propose_failed"
        )

    refactored_lint: dict[str, dict[str, Any]] = {}
    for path, code in proposals.items():
        refactored_lint[path] = json.loads(
            run_linter_tool.invoke({"code_string": code})
        )

    return {
        "refactored_code": proposals,
        "refactored_lint": refactored_lint,
        "revision_count": revision,
        "is_approved": False,
        "execution_status": "awaiting_approval",
        "error_message": "",
        "history": [
            _event(
                "refactor_proposed",
                revision=revision,
                feedback_applied=feedback,
                files=list(proposals),
            )
        ],
    }


def apply_changes_node(state: ReviewState, deps: Dependencies) -> dict[str, Any]:
    """Commit the approved refactoring to the PR branch (human approval only)."""
    if not state.get("is_approved"):
        return _error("Refusing to commit without explicit approval", "apply_refused")

    commits: list[dict[str, str]] = []
    try:
        client = deps.github_factory()
        for path, new_code in state["refactored_code"].items():
            if new_code == state["code_contents"].get(path):
                continue  # nothing to commit
            response = client.update_file(
                state["owner"],
                state["repository"],
                path,
                new_code,
                f"refactor: automated code review fixes for {path}",
                state["branch"],
                state["file_shas"][path],
            )
            commits.append(
                {"path": path, "sha": response.get("commit", {}).get("sha", "")}
            )
    except (GitHubError, KeyError) as exc:
        update = _error(f"Failed to commit changes: {exc}", "apply_failed")
        update["commits"] = commits
        return update

    return {
        "commits": commits,
        "execution_status": "completed",
        "error_message": "",
        "history": [_event("changes_committed", commits=commits)],
    }
