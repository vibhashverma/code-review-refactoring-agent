"""Webhook endpoint tests (FastAPI TestClient, mocked GitHub and LLM)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from main import create_app


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(service) -> TestClient:
    return TestClient(create_app(service))


def pr_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "number": 7,
            "html_url": "https://github.com/octo/demo/pull/7",
            "head": {"ref": "feature", "repo": {"full_name": "octo/demo"}},
            "extra": "ignored",
        },
        "repository": {
            "name": "demo",
            "full_name": "octo/demo",
            "html_url": "https://github.com/octo/demo",
            "owner": {"login": "octo"},
        },
    }


def post(client, payload, event="pull_request", headers=None):
    return client.post(
        "/api/webhooks/github",
        content=json.dumps(payload),
        headers={
            "X-GitHub-Event": event,
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )


def test_pr_opened_starts_review_and_pauses(client, github):
    response = post(client, pr_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    thread_id = body["thread_id"]
    assert thread_id.startswith("octo-demo-pr7-")

    status = client.get(f"/api/reviews/{thread_id}").json()
    assert status["status"] == "awaiting_approval"
    assert status["branch"] == "feature"
    assert github.called("update_file") == []


def test_each_webhook_gets_unique_thread_id(client):
    first = post(client, pr_payload()).json()["thread_id"]
    second = post(client, pr_payload("synchronize")).json()["thread_id"]
    assert first != second


def test_ping_event(client):
    response = post(client, {"zen": "Keep it logically awesome."}, event="ping")
    assert response.status_code == 202
    assert response.json()["status"] == "pong"


def test_unhandled_action_and_event_ignored(client):
    assert post(client, pr_payload("closed")).json()["status"] == "ignored"
    assert post(client, {"ref": "x"}, event="push").json()["status"] == "ignored"


def test_invalid_payload_rejected(client):
    assert post(client, {"action": "opened"}).status_code == 422
    bad = pr_payload()
    bad["pull_request"]["number"] = "not-a-number"
    assert post(client, bad).status_code == 422
    response = client.post(
        "/api/webhooks/github",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_github_api_error_is_recorded_in_review_state(client, github):
    github.fail_on = "get_pull_request"
    thread_id = post(client, pr_payload()).json()["thread_id"]

    status = client.get(f"/api/reviews/{thread_id}").json()
    assert status["status"] == "error"
    assert "Failed to fetch PR" in status["error_message"]


def test_signature_verification(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    payload = json.dumps(pr_payload())
    good = "sha256=" + hmac.new(b"s3cret", payload.encode(), hashlib.sha256).hexdigest()

    assert post(client, pr_payload()).status_code == 401
    assert (
        post(
            client, pr_payload(), headers={"X-Hub-Signature-256": "sha256=bad"}
        ).status_code
        == 401
    )
    ok = client.post(
        "/api/webhooks/github",
        content=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": good,
        },
    )
    assert ok.status_code == 202


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
