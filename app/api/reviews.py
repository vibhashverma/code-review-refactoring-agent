"""Human-in-the-loop review endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_review_service
from app.api.schemas import ApprovalRequest, ErrorResponse, ReviewStatus
from app.services.review_service import (
    ReviewConflictError,
    ReviewExecutionError,
    ReviewNotFoundError,
    ReviewService,
)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

ERRORS = {
    404: {"model": ErrorResponse, "description": "Unknown thread_id"},
    409: {"model": ErrorResponse, "description": "Review not awaiting approval"},
}


@router.get(
    "/{thread_id}",
    response_model=ReviewStatus,
    summary="Get review status and proposed changes",
    responses={404: ERRORS[404]},
)
def get_review(
    thread_id: str, service: ReviewService = Depends(get_review_service)
) -> dict:
    """Return the current state of a review thread."""
    try:
        return service.get_status(thread_id)
    except ReviewNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown thread {thread_id}")


@router.post(
    "/{thread_id}/approve",
    response_model=ReviewStatus,
    summary="Approve or reject the proposed refactoring",
    responses={
        **ERRORS,
        422: {"model": ErrorResponse, "description": "Invalid request body"},
        502: {"model": ErrorResponse, "description": "GitHub commit failed"},
    },
)
def decide(
    thread_id: str,
    body: ApprovalRequest,
    service: ReviewService = Depends(get_review_service),
) -> dict:
    """Approve (commit to GitHub) or reject (regenerate using feedback)."""
    try:
        return service.submit_decision(thread_id, body.approved, body.feedback)
    except ReviewNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown thread {thread_id}")
    except ReviewConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ReviewExecutionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
