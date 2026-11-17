"""Construction of the LangGraph review pipeline."""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import (
    Dependencies,
    apply_changes_node,
    default_dependencies,
    fetch_pr_node,
    lint_code_node,
    propose_refactor_node,
)
from app.agents.state import ReviewState

APPLY_NODE = "apply_changes_node"
PROPOSE_NODE = "propose_refactor_node"
LINT_NODE = "lint_code_node"
STOP_STATUSES = {"error", "skipped"}


def _continue_or_end(next_node: str):
    """Router: go to ``next_node`` unless the previous node failed/skipped."""

    def route(state: ReviewState) -> str:
        return END if state.get("execution_status") in STOP_STATUSES else next_node

    return route


def build_graph(checkpointer: Any, deps: Dependencies | None = None) -> Any:
    """Compile the review graph.

    Flow: fetch_pr -> lint_code -> propose_refactor -> [HUMAN] -> apply_changes.
    The graph is interrupted *before* ``apply_changes_node``; on rejection the
    service layer rewinds the same thread to ``lint_code_node`` so the next step
    is ``propose_refactor_node`` again (with feedback in state).
    """
    deps = deps or default_dependencies()
    builder = StateGraph(ReviewState)
    builder.add_node("fetch_pr_node", partial(fetch_pr_node, deps=deps))
    builder.add_node(LINT_NODE, partial(lint_code_node, deps=deps))
    builder.add_node(PROPOSE_NODE, partial(propose_refactor_node, deps=deps))
    builder.add_node(APPLY_NODE, partial(apply_changes_node, deps=deps))

    builder.add_edge(START, "fetch_pr_node")
    builder.add_conditional_edges(
        "fetch_pr_node", _continue_or_end(LINT_NODE), [LINT_NODE, END]
    )
    builder.add_edge(LINT_NODE, PROPOSE_NODE)
    builder.add_conditional_edges(
        PROPOSE_NODE, _continue_or_end(APPLY_NODE), [APPLY_NODE, END]
    )
    builder.add_edge(APPLY_NODE, END)

    return builder.compile(checkpointer=checkpointer, interrupt_before=[APPLY_NODE])
