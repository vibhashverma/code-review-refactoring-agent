"""Approval / rejection endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import create_app
from tests.conftest import COMPREHENSION_VERSION, LOOP_VERSION, PR_URL

FEEDBACK = "Use list comprehensions instead of loops"


@pytest.fixture
def client(service) -> TestClient:
    service.start_review("t1", PR_URL)
    return TestClient(create_app(service))


def decide(client, approved, feedback="", thread="t1"):
    return client.post(
        f"/api/reviews/{thread}/approve",
        json={"approved": approved, "feedback": feedback},
    )


def test_approve_commits_and_completes(client, github):
    response = decide(client, True)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["commits"] == [{"path": "app.py", "sha": "commit-123"}]
    assert github.called("update_file")[0][3] == LOOP_VERSION


def test_reject_regenerates_and_pauses_again(client, github):
    response = decide(client, False, FEEDBACK)

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "t1"
    assert body["status"] == "awaiting_approval"
    assert body["reviewer_feedback"] == FEEDBACK
    assert body["refactored_code"]["app.py"] == COMPREHENSION_VERSION
    assert github.called("update_file") == []

    # the paused thread can then be approved
    assert decide(client, True).json()["status"] == "completed"


def test_rejection_requires_feedback(client):
    assert decide(client, False, "  ").status_code == 422


def test_invalid_body(client):
    response = client.post("/api/reviews/t1/approve", json={"approved": "maybe"})
    assert response.status_code == 422
    assert client.post("/api/reviews/t1/approve", json={}).status_code == 422


def test_unknown_thread(client):
    assert decide(client, True, thread="nope").status_code == 404
    assert client.get("/api/reviews/nope").status_code == 404


def test_second_decision_conflicts(client):
    assert decide(client, True).status_code == 200
    assert decide(client, True).status_code == 409


def test_github_commit_error_returns_502(client, github):
    github.fail_on = "update_file"
    response = decide(client, True)

    assert response.status_code == 502
    assert "Failed to commit" in response.json()["detail"]
    assert client.get("/api/reviews/t1").json()["status"] == "error"
