"""LangGraph state schema for the review pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ReviewState(TypedDict, total=False):
    """State shared by all graph nodes.

    ``history`` uses an additive reducer, so nodes (and ``update_state``) only
    return *new* entries and the full audit trail is preserved on one thread.
    """

    # --- required keys ---
    pr_url: str
    code_contents: dict[str, str]  # file path -> original source
    lint_errors: dict[str, dict[str, Any]]  # file path -> linter result
    refactored_code: dict[str, str]  # file path -> proposed source
    is_approved: bool
    reviewer_feedback: str
    history: Annotated[list[dict[str, Any]], operator.add]

    # --- additional bookkeeping ---
    thread_id: str
    owner: str  # repository that holds the PR head branch
    repository: str
    branch: str
    base_branch: str
    pull_number: int
    file_paths: list[str]
    file_shas: dict[str, str]
    refactored_lint: dict[str, dict[str, Any]]
    revision_count: int
    commits: list[dict[str, str]]
    execution_status: str
    error_message: str
