"""FastAPI application entry point: ``uvicorn main:app``."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import reviews, webhooks
from app.config import get_settings
from app.services.review_service import ReviewService

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("app")


def build_default_service() -> ReviewService:
    """Create the production graph with a persistent SQLite checkpointer."""
    from app.agents.checkpointer import create_checkpointer
    from app.agents.graph import build_graph

    settings = get_settings()
    graph = build_graph(create_checkpointer(settings.checkpoint_db_path))
    return ReviewService(graph, settings)


def create_app(review_service: ReviewService | None = None) -> FastAPI:
    """Application factory; tests inject a ``ReviewService`` with fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        for name in settings.missing_required():
            logger.warning("Required environment variable %s is not set", name)
        if not settings.github_webhook_secret.get_secret_value():
            logger.warning("GITHUB_WEBHOOK_SECRET not set: signatures not verified")
        if getattr(app.state, "review_service", None) is None:
            app.state.review_service = build_default_service()
        yield

    app = FastAPI(
        title="Autonomous Code Review & Refactoring Agent",
        description=(
            "LangGraph agent that lints and refactors Python code in GitHub pull "
            "requests, pausing for human approval before committing."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.review_service = review_service
    app.include_router(webhooks.router)
    app.include_router(reviews.router)

    @app.get("/health", tags=["health"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
