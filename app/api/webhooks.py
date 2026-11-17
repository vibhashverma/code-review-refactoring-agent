"""GitHub webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi import status

from app.api.deps import get_review_service
from app.api.schemas import ErrorResponse, PullRequestEvent, WebhookResponse
from app.config import get_settings
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


async def verify_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> None:
    """Validate ``X-Hub-Signature-256`` when a webhook secret is configured."""
    secret = get_settings().github_webhook_secret.get_secret_value()
    if not secret:
        return
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not x_hub_signature_256 or not hmac.compare_digest(
        expected, x_hub_signature_256
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")


def _run_review(service: ReviewService, thread_id: str, pr_url: str) -> None:
    """Background task wrapper; failures are logged, never raised."""
    try:
        service.start_review(thread_id, pr_url)
    except Exception:  # state of failed runs is visible via the status endpoint
        logger.exception("Review failed for thread %s", thread_id)


@router.post(
    "/github",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive GitHub pull_request webhook events",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid signature"},
        422: {"model": ErrorResponse, "description": "Invalid payload"},
    },
)
async def github_webhook(
    payload: PullRequestEvent,
    background: BackgroundTasks,
    x_github_event: str = Header(default="pull_request"),
    service: ReviewService = Depends(get_review_service),
    _: None = Depends(verify_signature),
) -> WebhookResponse:
    """Start a review for opened/updated pull requests; ignore other events."""
    if x_github_event == "ping":
        return WebhookResponse(status="pong", message="Webhook configured")
    if x_github_event != "pull_request":
        return WebhookResponse(status="ignored", message=f"Event {x_github_event}")
    if not (payload.action and payload.pull_request and payload.repository):
        raise HTTPException(
            422,
            "pull_request event requires action, pull_request and repository",
        )
    if payload.action not in HANDLED_ACTIONS:
        return WebhookResponse(status="ignored", message=f"Action {payload.action}")

    repo = payload.repository
    pr = payload.pull_request
    thread_id = f"{repo.owner.login}-{repo.name}-pr{pr.number}-{uuid.uuid4().hex[:8]}"
    service.mark_started(thread_id)
    background.add_task(_run_review, service, thread_id, pr.html_url)
    logger.info(
        "Accepted PR webhook %s#%s branch=%s thread=%s",
        repo.full_name,
        pr.number,
        pr.head.ref,
        thread_id,
    )
    return WebhookResponse(
        thread_id=thread_id,
        status="processing",
        message=f"Review started for {repo.full_name}#{pr.number} ({pr.head.ref})",
    )
