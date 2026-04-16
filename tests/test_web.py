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

    def test_finalize_trims_to_prioritized_count(self):
        """Final prioritized list is the source of truth — flag_count must equal
        len(flags) so the drill-down header and list never disagree.
        """
        from qbr_web.app import _finalize_project_state, project_state

        # Simulate incremental state that accumulated more flags than the final
        # prioritized list will contain (prioritize_flags truncates to top 10).
        project_state["Project Phoenix"] = {"flag_count": 15, "flags": []}

        final_flags = [self._make_flag("high")]
        _finalize_project_state({"Project Phoenix": final_flags}, job_id="job5")

        entry = project_state["Project Phoenix"]
        assert entry["flag_count"] == 1  # trusts the final prioritized list
        assert len(entry["flags"]) == 1  # flag_count and list length match


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


class TestProjectsStateEndpoint:
    """Unit 3: GET /api/projects/state returns the live dashboard payload."""

    def test_empty_state_returns_all_seeds_unknown(self, client):
        resp = client.get("/api/projects/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is False
        assert data["active_project"] is None
        # All three seed projects present with unknown health
        seed_names = {"Project Phoenix", "Project Omicron", "DivatKirály"}
        assert set(data["projects"].keys()) == seed_names
        for _, entry in data["projects"].items():
            assert entry["health"] == "unknown"

    def test_state_reflects_finalized_project(self, client):
        from datetime import UTC, datetime

        from qbr.models import (
            AttentionFlag,
            FlagStatus,
            FlagType,
            Severity,
            SourceAttribution,
        )

        # Set up live state for one project
        from qbr_web.app import _finalize_project_state

        flag = AttentionFlag(
            flag_type=FlagType.UNRESOLVED_ACTION,
            title="t",
            severity=Severity.CRITICAL,
            project="Project Phoenix",
            sources=[
                SourceAttribution(
                    person="Alice",
                    email="a@x.com",
                    timestamp=datetime.now(UTC),
                )
            ],
            status=FlagStatus.OPEN,
        )
        _finalize_project_state({"Project Phoenix": [flag]}, job_id="jX")

        resp = client.get("/api/projects/state")
        data = resp.json()
        assert data["projects"]["Project Phoenix"]["health"] == "critical"
        assert data["projects"]["Project Phoenix"]["flag_count"] == 1
        # flags list is NOT returned (polling payload is lean)
        assert "flags" not in data["projects"]["Project Phoenix"]

    def test_is_running_and_active_project_with_processing_job(self, client):
        from qbr_web.app import jobs

        jobs["j1"] = {
            "id": "j1",
            "source": "demo",
            "state": "processing",
            "progress": [],
            "result": None,
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
            "active_project": "Project Phoenix",
        }
        try:
            resp = client.get("/api/projects/state")
            data = resp.json()
            assert data["is_running"] is True
            assert data["active_project"] == "Project Phoenix"
        finally:
            del jobs["j1"]

    def test_completed_job_does_not_mark_running(self, client):
        from qbr_web.app import jobs

        jobs["j2"] = {
            "id": "j2",
            "source": "demo",
            "state": "complete",
            "progress": [],
            "result": {"usage": {}},
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
            "active_project": None,
        }
        try:
            resp = client.get("/api/projects/state")
            data = resp.json()
            assert data["is_running"] is False
            assert data["active_project"] is None
        finally:
            del jobs["j2"]

    def test_uploaded_project_not_in_seed_is_included(self, client):
        from qbr_web.app import _merge_incremental_flags

        _merge_incremental_flags("Custom Uploaded Project", [], "jU")
        resp = client.get("/api/projects/state")
        data = resp.json()
        assert "Custom Uploaded Project" in data["projects"]


class TestDashboardLiveRender:
    """Unit 4: server-rendered dashboard reflects live project_state on first paint."""

    def _make_flag(self, severity, project):
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
            title="t",
            severity=sev_map[severity],
            project=project,
            sources=[
                SourceAttribution(
                    person="A",
                    email="a@x.com",
                    timestamp=datetime.now(UTC),
                )
            ],
            status=FlagStatus.OPEN,
        )

    def test_empty_state_shows_pending_analysis(self, client):
        """Before any run: all seed cards show 'Pending analysis'."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.text.count("Pending analysis") >= 3  # three seed cards

    def test_grid_has_is_running_data_attribute(self, client):
        resp = client.get("/")
        assert 'data-is-running="false"' in resp.text
        assert 'id="project-grid"' in resp.text

    def test_cards_have_project_name_attribute(self, client):
        resp = client.get("/")
        assert 'data-project-name="Project Phoenix"' in resp.text
        assert 'data-project-name="Project Omicron"' in resp.text
        assert 'data-project-name="DivatKir' in resp.text  # partial match handles encoding

    def test_critical_project_renders_with_health_label(self, client):
        from qbr_web.app import _finalize_project_state

        _finalize_project_state(
            {"Project Phoenix": [self._make_flag("critical", "Project Phoenix")]}, "j1"
        )
        resp = client.get("/")
        assert "Critical — act now" in resp.text
        assert "1 flags" in resp.text
        assert "1 critical" in resp.text

    def test_warning_not_good_for_medium_flags(self, client):
        from qbr_web.app import _finalize_project_state

        _finalize_project_state(
            {"Project Omicron": [self._make_flag("medium", "Project Omicron")]}, "j2"
        )
        resp = client.get("/")
        assert "Attention needed" in resp.text  # warning label
        # Should NOT be labeled "On track" for this project
        # (Other projects still show Pending analysis, which is fine)

    def test_running_state_renders_data_is_running_true(self, client):
        from qbr_web.app import jobs

        jobs["j1"] = {
            "id": "j1",
            "source": "demo",
            "state": "processing",
            "progress": [],
            "result": None,
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
            "active_project": "Project Phoenix",
        }
        try:
            resp = client.get("/")
            assert 'data-is-running="true"' in resp.text
            assert 'data-active-project="Project Phoenix"' in resp.text
            # Active badge rendered on matching card
            assert "Analysis in progress" in resp.text
        finally:
            del jobs["j1"]

    def test_flash_class_on_active_card_only(self, client):
        import re

        from qbr_web.app import jobs

        jobs["j1"] = {
            "id": "j1",
            "source": "demo",
            "state": "processing",
            "progress": [],
            "result": None,
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
            "active_project": "Project Phoenix",
        }
        try:
            resp = client.get("/")
            # Count only `.project-card` elements with the flash class applied.
            cards_with_flash = re.findall(
                r'<div class="project-card[^"]*qbr-active-flash[^"]*"[^>]*data-project-name="([^"]+)"',
                resp.text,
            )
            assert cards_with_flash == ["Project Phoenix"]
        finally:
            del jobs["j1"]

    def test_drill_down_link_url_encoded(self, client):
        """Project names with non-ASCII chars are URL-encoded in links."""
        resp = client.get("/")
        # DivatKirály → DivatKir%C3%A1ly (UTF-8 encoded)
        assert "/projects/DivatKir%C3%A1ly" in resp.text

    def test_drill_down_link_escapes_slash(self, client):
        """strict_urlencode also escapes '/' so project names with slashes
        don't silently produce a 404-routing link."""
        from qbr_web.app import _merge_incremental_flags

        _merge_incremental_flags("foo/bar", [], "jSlash")
        resp = client.get("/")
        # '/' should be %2F in the href, not a literal '/'
        assert "/projects/foo%2Fbar" in resp.text
        assert 'href="/projects/foo/bar"' not in resp.text

    def test_aria_live_region_present(self, client):
        resp = client.get("/")
        assert 'id="dashboard-live-region"' in resp.text
        assert 'aria-live="polite"' in resp.text

    def test_details_not_nested_inside_anchor(self, client):
        """<details> must live OUTSIDE the drill-down <a> to remain interactive."""
        import re

        resp = client.get("/")
        # Find the first project card block
        card_match = re.search(
            r'data-project-name="Project Phoenix"(.*?)(?=data-project-name=|</main>)',
            resp.text,
            re.DOTALL,
        )
        assert card_match is not None
        card_html = card_match.group(1)

        # Anchor opens at `<a href="/projects/...` and closes at the first `</a>`.
        anchor_open = card_html.find('<a href="/projects/')
        assert anchor_open >= 0
        anchor_close = card_html.find("</a>", anchor_open)
        assert anchor_close >= 0

        # <details> (if present) must be AFTER the anchor's </a>.
        details_pos = card_html.find("<details", anchor_open)
        if details_pos >= 0:
            assert details_pos > anchor_close, (
                "<details> must not be nested inside the card's drill-down <a>"
            )


class TestProjectDrilldown:
    """Unit 5: GET /projects/{name} drill-down page."""

    def _make_flag(self, severity, project, status="open", title="flag"):
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
        st_map = {
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
                    email="alice@x.com",
                    timestamp=datetime.now(UTC),
                    source_ref="email1.txt",
                )
            ],
            status=st_map[status],
            evidence_summary='"Payment gateway broken" — Alice (email1.txt)',
            age_days=5,
        )

    def test_seed_project_never_analyzed_shows_empty_state(self, client):
        resp = client.get("/projects/Project Phoenix")
        assert resp.status_code == 200
        assert "No analysis yet for Project Phoenix" in resp.text
        assert "Run Analysis" in resp.text

    def test_analyzed_project_with_flags_lists_them(self, client):
        from qbr_web.app import _finalize_project_state

        _finalize_project_state(
            {
                "Project Phoenix": [
                    self._make_flag("critical", "Project Phoenix", "open", "Flag A"),
                    self._make_flag("high", "Project Phoenix", "needs_review", "Flag B"),
                ]
            },
            job_id="jX",
        )
        resp = client.get("/projects/Project Phoenix")
        assert resp.status_code == 200
        assert "Flag A" in resp.text
        assert "Flag B" in resp.text
        assert "Payment gateway broken" in resp.text  # evidence_summary
        assert "Critical — act now" in resp.text  # health label
        # Canonical status-count order
        assert "1 open" in resp.text
        assert "1 needs review" in resp.text
        assert "0 resolved" in resp.text

    def test_analyzed_project_zero_flags_shows_all_clear(self, client):
        from qbr_web.app import _finalize_project_state

        _finalize_project_state({"Project Phoenix": []}, job_id="jY")
        resp = client.get("/projects/Project Phoenix")
        assert resp.status_code == 200
        assert "All clear" in resp.text
        # No flag items rendered
        assert "Attention Flags</h2>" not in resp.text

    def test_unknown_project_returns_404(self, client):
        resp = client.get("/projects/NoSuchProject")
        assert resp.status_code == 404

    def test_non_ascii_project_name_round_trip(self, client):
        # Seed project name "DivatKirály" with URL-encoded UTF-8
        resp = client.get("/projects/DivatKir%C3%A1ly")
        assert resp.status_code == 200
        assert "DivatKirály" in resp.text

    def test_evicted_job_renders_disabled_report_link(self, client):
        from qbr_web.app import _finalize_project_state

        _finalize_project_state(
            {"Project Phoenix": [self._make_flag("high", "Project Phoenix")]},
            job_id="evicted_job_id",  # job not in jobs dict
        )
        resp = client.get("/projects/Project Phoenix")
        assert resp.status_code == 200
        assert "Report no longer available" in resp.text
        # The disabled report link must NOT include a clickable URL
        assert 'href="/jobs/evicted_job_id/report"' not in resp.text

    def test_completed_job_renders_active_report_link(self, client):
        from qbr_web.app import _finalize_project_state, jobs

        jobs["live_job"] = {
            "id": "live_job",
            "source": "demo",
            "state": "complete",
            "progress": [],
            "result": {"usage": {}},
            "error": None,
            "created_at": "2026-04-16T00:00:00+00:00",
            "active_project": None,
        }
        _finalize_project_state(
            {"Project Phoenix": [self._make_flag("high", "Project Phoenix")]},
            job_id="live_job",
        )
        try:
            resp = client.get("/projects/Project Phoenix")
            assert resp.status_code == 200
            assert 'href="/jobs/live_job/report"' in resp.text
            assert "View full report" in resp.text
        finally:
            del jobs["live_job"]
