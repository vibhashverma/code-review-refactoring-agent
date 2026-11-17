"""LangGraph workflow, checkpoint persistence, approval and rejection tests."""

from __future__ import annotations

import pytest

from app.agents.graph import APPLY_NODE
from app.services.review_service import ReviewConflictError, ReviewExecutionError
from tests.conftest import COMPREHENSION_VERSION, LOOP_VERSION, PR_URL

FEEDBACK = "Use list comprehensions instead of loops"


def events(status: dict) -> list[str]:
    return [h["event"] for h in status["history"]]


def test_graph_runs_nodes_and_pauses_before_apply(service, github, llm):
    status = service.start_review("t1", PR_URL)

    assert status["status"] == "awaiting_approval"
    assert events(status) == [
        "review_started",
        "pr_fetched",
        "code_linted",
        "refactor_proposed",
    ]
    # fetch_pr_node: only the modified, non-removed .py file is fetched
    assert [c[2] for c in github.called("get_file_content")] == ["app.py"]
    # lint_code_node: original code has issues
    assert status["lint_errors"]["app.py"]["ok"] is False
    # propose_refactor_node ran once
    assert len(llm.prompts) == 1
    assert status["refactored_code"]["app.py"] == LOOP_VERSION
    assert status["refactored_lint"]["app.py"]["ok"] is True
    # interrupt_before=apply_changes_node: nothing committed
    assert github.called("update_file") == []
    snapshot = service._graph.get_state({"configurable": {"thread_id": "t1"}})
    assert snapshot.next == (APPLY_NODE,)


def test_rejection_loops_on_same_thread_with_feedback(service, github, llm):
    service.start_review("t1", PR_URL)

    status = service.submit_decision("t1", approved=False, feedback=FEEDBACK)

    assert status["thread_id"] == "t1"
    assert status["reviewer_feedback"] == FEEDBACK
    assert status["status"] == "awaiting_approval"  # paused again
    assert status["revision_count"] == 2
    assert len(llm.prompts) == 2  # propose node executed again
    assert FEEDBACK in llm.prompts[1]
    assert LOOP_VERSION in llm.prompts[1]  # previous proposal shown to the LLM
    assert status["refactored_code"]["app.py"] == COMPREHENSION_VERSION
    # graph was NOT restarted: PR fetched exactly once, history preserved
    assert len(github.called("get_pull_request")) == 1
    assert events(status) == [
        "review_started",
        "pr_fetched",
        "code_linted",
        "refactor_proposed",
        "human_rejected",
        "refactor_proposed",
    ]
    assert github.called("update_file") == []


def test_approval_commits_and_completes(service, github):
    service.start_review("t1", PR_URL)

    status = service.submit_decision("t1", approved=True)

    assert status["status"] == "completed"
    assert status["is_approved"] is True
    commit = github.called("update_file")
    assert len(commit) == 1
    owner, repo, path, content, message, branch, sha = commit[0]
    assert (owner, repo, path, branch, sha) == (
        "octo",
        "demo",
        "app.py",
        "feature",
        "sha-original",
    )
    assert content == LOOP_VERSION
    assert "refactor" in message
    assert status["commits"] == [{"path": "app.py", "sha": "commit-123"}]
    assert events(status)[-2:] == ["human_approved", "changes_committed"]


def test_reject_then_approve_commits_revised_code(service, github):
    service.start_review("t1", PR_URL)
    service.submit_decision("t1", False, FEEDBACK)
    status = service.submit_decision("t1", True)

    assert status["status"] == "completed"
    assert github.called("update_file")[0][3] == COMPREHENSION_VERSION


def test_state_survives_restart(make_service, github):
    make_service().start_review("t1", PR_URL)

    restarted = make_service()  # new graph + new SQLite connection, same file
    assert restarted.get_status("t1")["status"] == "awaiting_approval"
    status = restarted.submit_decision("t1", approved=True)

    assert status["status"] == "completed"
    assert len(github.called("update_file")) == 1


def test_cannot_decide_twice(service):
    service.start_review("t1", PR_URL)
    service.submit_decision("t1", True)
    with pytest.raises(ReviewConflictError):
        service.submit_decision("t1", True)


def test_revision_limit_prevents_infinite_loop(service):
    service.start_review("t1", PR_URL)
    service._settings = service._settings.model_copy(update={"max_revisions": 2})
    service.submit_decision("t1", False, "again")
    with pytest.raises(ReviewConflictError):
        service.submit_decision("t1", False, "again")


def test_github_fetch_error_ends_graph_with_error(make_service, github):
    github.fail_on = "get_pull_request"
    status = make_service().start_review("t1", PR_URL)

    assert status["status"] == "error"
    assert "Failed to fetch PR" in status["error_message"]
    assert status["refactored_code"] == {}


def test_commit_failure_reports_error(make_service, github):
    service = make_service()
    service.start_review("t1", PR_URL)
    github.fail_on = "update_file"

    with pytest.raises(ReviewExecutionError):
        service.submit_decision("t1", True)
    assert service.get_status("t1")["status"] == "error"
