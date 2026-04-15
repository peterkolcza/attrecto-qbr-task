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
        assert "Run Demo" in resp.text

    def test_index_has_upload_form(self, client):
        resp = client.get("/")
        assert "upload" in resp.text.lower() or "Upload" in resp.text


class TestAnalyze:
    def test_start_demo_analysis(self, client):
        resp = client.post("/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"

    def test_job_detail_page(self, client):
        # Start a job first
        resp = client.post("/analyze")
        job_id = resp.json()["job_id"]

        # Check job detail page
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert job_id in resp.text

    def test_job_not_found(self, client):
        resp = client.get("/jobs/nonexistent")
        assert resp.status_code == 404


class TestSSEStream:
    """SSE streams can't be tested with sync TestClient — verified manually."""

    pass
