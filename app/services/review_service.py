"""Review orchestration: starts graph runs and handles human decisions."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.agents.graph import APPLY_NODE, LINT_NODE, PROPOSE_NODE
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ReviewNotFoundError(Exception):
    """No review exists for the given thread_id."""


class ReviewConflictError(Exception):
    """The review is not in a state that allows the requested action."""


class ReviewExecutionError(Exception):
    """The graph finished in an error state (e.g. GitHub commit failed)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewService:
    """Facade over the compiled graph; one ``thread_id`` per PR review."""

    def __init__(self, graph: Any, settings: Settings | None = None) -> None:
        self._graph = graph
        self._settings = settings or get_settings()
        self._inflight: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _lock_for(self, thread_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(thread_id, threading.Lock())

    def _status_of(self, thread_id: str, snapshot: Any) -> str:
        if not snapshot.values:
            return "processing" if thread_id in self._inflight else "unknown"
        if APPLY_NODE in snapshot.next:
            return "awaiting_approval"
        if thread_id in self._inflight:
            return "processing"
        return snapshot.values.get("execution_status", "unknown")

    # ------------------------------------------------------------------ start
    def mark_started(self, thread_id: str) -> None:
        """Register a thread as running so status polls don't return 404."""
        self._inflight.add(thread_id)

    def start_review(self, thread_id: str, pr_url: str) -> dict[str, Any]:
        """Run the graph from the start until it pauses (or ends)."""
        self._inflight.add(thread_id)
        initial = {
            "pr_url": pr_url,
            "thread_id": thread_id,
            "code_contents": {},
            "lint_errors": {},
            "refactored_code": {},
            "is_approved": False,
            "reviewer_feedback": "",
            "history": [{"event": "review_started", "at": _now(), "pr_url": pr_url}],
            "revision_count": 0,
            "execution_status": "started",
        }
        try:
            self._graph.invoke(initial, self._config(thread_id))
        except Exception:
            logger.exception("Review run crashed for thread %s", thread_id)
            raise
        finally:
            self._inflight.discard(thread_id)
        return self.get_status(thread_id)

    # ----------------------------------------------------------------- status
    def get_status(self, thread_id: str) -> dict[str, Any]:
        """Return a JSON-friendly summary of the review."""
        snapshot = self._graph.get_state(self._config(thread_id))
        if not snapshot.values and thread_id not in self._inflight:
            raise ReviewNotFoundError(thread_id)
        values = snapshot.values or {}
        return {
            "thread_id": thread_id,
            "status": self._status_of(thread_id, snapshot),
            "pr_url": values.get("pr_url"),
            "branch": values.get("branch"),
            "revision_count": values.get("revision_count", 0),
            "files": values.get("file_paths", []),
            "lint_errors": values.get("lint_errors", {}),
            "refactored_code": values.get("refactored_code", {}),
            "refactored_lint": values.get("refactored_lint", {}),
            "reviewer_feedback": values.get("reviewer_feedback", ""),
            "is_approved": values.get("is_approved", False),
            "commits": values.get("commits", []),
            "error_message": values.get("error_message", ""),
            "history": values.get("history", []),
        }

    # --------------------------------------------------------------- decision
    def submit_decision(
        self, thread_id: str, approved: bool, feedback: str = ""
    ) -> dict[str, Any]:
        """Apply a human decision to a paused review on the *same* thread."""
        config = self._config(thread_id)
        with self._lock_for(thread_id):
            snapshot = self._graph.get_state(config)
            if not snapshot.values:
                raise ReviewNotFoundError(thread_id)
            if APPLY_NODE not in snapshot.next:
                raise ReviewConflictError(
                    f"Review is '{self._status_of(thread_id, snapshot)}', "
                    "not awaiting approval"
                )
            decision = {
                "event": "human_approved" if approved else "human_rejected",
                "at": _now(),
                "feedback": feedback,
            }
            if approved:
                self._graph.update_state(
                    config,
                    {
                        "is_approved": True,
                        "reviewer_feedback": "",
                        "history": [decision],
                    },
                    as_node=PROPOSE_NODE,
                )
            else:
                revisions = snapshot.values.get("revision_count", 0)
                if revisions >= self._settings.max_revisions:
                    raise ReviewConflictError(
                        f"Maximum of {self._settings.max_revisions} revisions reached"
                    )
                # Rewind this thread to just before propose_refactor_node.
                self._graph.update_state(
                    config,
                    {
                        "is_approved": False,
                        "reviewer_feedback": feedback,
                        "history": [decision],
                    },
                    as_node=LINT_NODE,
                )
            self._inflight.add(thread_id)
            try:
                self._graph.invoke(None, config)
            finally:
                self._inflight.discard(thread_id)

        result = self.get_status(thread_id)
        if result["status"] == "error":
            raise ReviewExecutionError(result["error_message"])
        return result
