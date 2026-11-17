"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.services.review_service import ReviewService


def get_review_service(request: Request) -> ReviewService:
    """Return the application-wide ``ReviewService``."""
    return request.app.state.review_service
