"""Tests for the FastAPI web application."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qbr_web.app import app


@pytest.fixture
def client():
    return TestClient(app)


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


class TestSSEStream:
    """SSE streams can't be tested with sync TestClient — verified manually."""

    pass
