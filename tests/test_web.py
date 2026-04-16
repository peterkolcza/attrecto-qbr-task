"""Tests for the FastAPI web application."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qbr_web.app import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_jobs():
    """Clear the in-memory job store before each test for isolation."""
    from qbr_web.app import jobs

    jobs.clear()
    yield
    jobs.clear()


class TestHealthcheck:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data


class TestIndex:
    def test_index_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "QBR" in resp.text
        assert "Process Demo Emails" in resp.text

    def test_index_has_upload_form(self, client):
        resp = client.get("/")
        assert "upload" in resp.text.lower() or "Upload" in resp.text


class TestAnalyze:
    def test_start_demo_analysis_redirects(self, client):
        resp = client.post("/analyze", follow_redirects=False)
        assert resp.status_code == 303
        assert "/jobs/" in resp.headers["location"]

    def test_start_demo_analysis_follow(self, client):
        resp = client.post("/analyze")  # follows redirect by default
        assert resp.status_code == 200
        assert "Job" in resp.text
        assert "Processing" in resp.text

    def test_job_detail_page(self, client):
        # Start a job first (follow redirect to get job page)
        resp = client.post("/analyze")
        assert resp.status_code == 200
        assert "Processing Log" in resp.text

    def test_job_not_found(self, client):
        resp = client.get("/jobs/nonexistent")
        assert resp.status_code == 404

    def test_duplicate_demo_returns_existing_job(self, client):
        """Clicking 'Process Demo Emails' while demo is running should redirect to existing job."""
        from qbr_web.app import jobs

        # Pre-create a running demo job
        jobs["abc12345"] = {
            "id": "abc12345",
            "source": "demo",
            "state": "processing",
            "progress": [],
            "result": None,
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
        }
        try:
            resp = client.post("/analyze", follow_redirects=False)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/jobs/abc12345"
        finally:
            del jobs["abc12345"]


class TestSSEStream:
    """SSE streams can't be tested with sync TestClient — verified manually."""

    pass
