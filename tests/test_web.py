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
    """Clear the in-memory job and project state stores before each test for isolation."""
    from qbr_web.app import jobs, project_state

    jobs.clear()
    project_state.clear()
    yield
    jobs.clear()
    project_state.clear()


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


class TestProjectStateFinalize:
    """Unit 1: _finalize_project_state populates the live dashboard store."""

    def _make_flag(self, severity, title="t", project="P", status="open"):
        from datetime import UTC, datetime

        from qbr.models import (
            AttentionFlag,
            FlagStatus,
            FlagType,
            Severity,
            SourceAttribution,
        )

        sev_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
        }
        status_map = {
            "open": FlagStatus.OPEN,
            "needs_review": FlagStatus.NEEDS_REVIEW,
            "resolved": FlagStatus.RESOLVED,
        }
        return AttentionFlag(
            flag_type=FlagType.UNRESOLVED_ACTION,
            title=title,
            severity=sev_map[severity],
            project=project,
            sources=[
                SourceAttribution(
                    person="Alice",
                    email="alice@example.com",
                    timestamp=datetime.now(UTC),
                    source_ref="email1.txt",
                    quoted_text="test quote",
                )
            ],
            status=status_map[status],
        )

    def test_finalize_sets_critical_health(self):
        from qbr_web.app import _finalize_project_state, project_state

        flags = [self._make_flag("critical"), self._make_flag("high")]
        _finalize_project_state({"Project Phoenix": flags}, job_id="job1")

        entry = project_state["Project Phoenix"]
        assert entry["health"] == "critical"
        assert entry["flag_count"] == 2
        assert entry["critical_count"] == 1
        assert entry["high_count"] == 1
        assert entry["last_updated"]  # non-empty ISO string
        assert entry["latest_job_id"] == "job1"
        assert isinstance(entry["flags"], list)
        assert len(entry["flags"]) == 2
        # Flags are JSON-safe dicts (no datetime objects)
        import json

        json.dumps(entry["flags"])  # must not raise

    def test_finalize_medium_only_is_warning_not_good(self):
        """medium/low severity must NOT collapse into 'good' — masks signal."""
        from qbr_web.app import _finalize_project_state, project_state

        flags = [self._make_flag("medium"), self._make_flag("low")]
        _finalize_project_state({"Project Omicron": flags}, job_id="job2")

        assert project_state["Project Omicron"]["health"] == "warning"

    def test_finalize_empty_flag_list_is_good(self):
        from qbr_web.app import _finalize_project_state, project_state

        _finalize_project_state({"DivatKirály": []}, job_id="job3")

        entry = project_state["DivatKirály"]
        assert entry["health"] == "good"
        assert entry["flag_count"] == 0
        assert entry["critical_count"] == 0

    def test_finalize_does_not_overwrite_unprocessed_projects(self):
        """Projects not in flags_by_project should not appear in state."""
        from qbr_web.app import _finalize_project_state, project_state

        _finalize_project_state({"Project Phoenix": []}, job_id="job4")
        assert "Project Phoenix" in project_state
        assert "Project Omicron" not in project_state
        assert "DivatKirály" not in project_state

    def test_finalize_preserves_higher_incremental_count(self):
        """If Unit 2's incremental count is higher (defensive), keep the higher."""
        from qbr_web.app import _finalize_project_state, project_state

        # Simulate incremental state from Unit 2
        project_state["Project Phoenix"] = {"flag_count": 5}

        _finalize_project_state({"Project Phoenix": [self._make_flag("high")]}, job_id="job5")
        assert project_state["Project Phoenix"]["flag_count"] == 5  # kept higher


class TestIncrementalFlagMerge:
    """Unit 2: _merge_incremental_flags writes as threads are classified."""

    def _make_flag(self, severity, title="t", project="P"):
        from datetime import UTC, datetime

        from qbr.models import (
            AttentionFlag,
            FlagStatus,
            FlagType,
            Severity,
            SourceAttribution,
        )

        sev_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
        }
        return AttentionFlag(
            flag_type=FlagType.UNRESOLVED_ACTION,
            title=title,
            severity=sev_map[severity],
            project=project,
            sources=[
                SourceAttribution(
                    person="Bob",
                    email="bob@example.com",
                    timestamp=datetime.now(UTC),
                    source_ref="email2.txt",
                )
            ],
            status=FlagStatus.OPEN,
        )

    def test_merge_from_scratch_sets_counts(self):
        from qbr_web.app import _merge_incremental_flags, project_state

        flags = [self._make_flag("critical"), self._make_flag("high")]
        _merge_incremental_flags("Project Phoenix", flags, job_id="j1")

        entry = project_state["Project Phoenix"]
        assert entry["flag_count"] == 2
        assert entry["critical_count"] == 1
        assert entry["high_count"] == 1
        assert entry["health"] == "critical"
        assert entry["latest_job_id"] == "j1"

    def test_merge_accumulates_across_threads(self):
        from qbr_web.app import _merge_incremental_flags, project_state

        _merge_incremental_flags("Project Phoenix", [self._make_flag("medium")], "j1")
        _merge_incremental_flags("Project Phoenix", [self._make_flag("high")], "j1")

        entry = project_state["Project Phoenix"]
        assert entry["flag_count"] == 2
        assert entry["medium_count"] == 1
        assert entry["high_count"] == 1
        assert entry["health"] == "warning"

    def test_merge_empty_flags_still_registers_project(self):
        """So the dashboard can show an 'active' flash even before first flag."""
        from qbr_web.app import _merge_incremental_flags, project_state

        _merge_incremental_flags("DivatKirály", [], job_id="j2")

        assert "DivatKirály" in project_state
        assert project_state["DivatKirály"]["flag_count"] == 0
        assert project_state["DivatKirály"]["health"] == "good"

    def test_job_dict_has_active_project_key(self, client):
        """Creating a job initializes active_project=None for the dashboard."""
        from qbr_web.app import jobs

        client.post("/analyze", follow_redirects=False)
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert "active_project" in job
        # Starts None, but may have flipped by now since the pipeline runs async.
        # The key's presence is what matters for the dashboard contract.
