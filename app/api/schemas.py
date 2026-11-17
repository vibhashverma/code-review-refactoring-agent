"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorResponse(BaseModel):
    """Standard error body."""

    detail: str


class Owner(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str


class RepositoryInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    full_name: str
    html_url: str
    owner: Owner


class BranchRepo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    full_name: str


class BranchRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ref: str
    repo: BranchRepo | None = None


class PullRequestInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int
    html_url: str
    head: BranchRef


class PullRequestEvent(BaseModel):
    """Subset of the GitHub ``pull_request`` webhook payload we rely on.

    Fields are optional so that ``ping`` events validate; the endpoint enforces
    presence for ``pull_request`` events.
    """

    model_config = ConfigDict(extra="ignore")
    action: str | None = None
    number: int | None = None
    pull_request: PullRequestInfo | None = None
    repository: RepositoryInfo | None = None
    zen: str | None = None


class WebhookResponse(BaseModel):
    """Result of receiving a webhook."""

    thread_id: str | None = Field(None, description="Review session identifier")
    status: str = Field(..., examples=["processing"])
    message: str = ""


class ApprovalRequest(BaseModel):
    """Human decision on a proposed refactoring."""

    approved: bool
    feedback: str = Field("", max_length=4000)

    @model_validator(mode="after")
    def feedback_required_on_rejection(self) -> "ApprovalRequest":
        if not self.approved and not self.feedback.strip():
            raise ValueError("feedback is required when rejecting a proposal")
        return self


class ReviewStatus(BaseModel):
    """Full review state returned by the status and approval endpoints."""

    thread_id: str
    status: str = Field(
        ...,
        description="processing | awaiting_approval | completed | skipped | error",
    )
    pr_url: str | None = None
    branch: str | None = None
    revision_count: int = 0
    files: list[str] = []
    lint_errors: dict[str, Any] = {}
    refactored_code: dict[str, str] = {}
    refactored_lint: dict[str, Any] = {}
    reviewer_feedback: str = ""
    is_approved: bool = False
    commits: list[dict[str, str]] = []
    error_message: str = ""
    history: list[dict[str, Any]] = []
