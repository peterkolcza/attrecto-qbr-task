"""Tests for the Portfolio Health Report generator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 — used in pytest tmp_path fixture
from unittest.mock import MagicMock

from qbr.models import AttentionFlag, FlagType, SourceAttribution, SourceType
from qbr.report import (
    _flags_to_json,
    build_report_json,
    generate_report,
    save_report,
)


def _make_flag(
    title: str = "Test flag",
    flag_type: FlagType = FlagType.UNRESOLVED_ACTION,
    severity: str = "high",
    project: str = "TestProject",
) -> AttentionFlag:
    return AttentionFlag(
        flag_type=flag_type,
        title=title,
        severity=severity,
        project=project,
        sources=[
            SourceAttribution(
                person="Alice",
                email="alice@test.com",
                role="PM",
                timestamp=datetime(2025, 6, 1, tzinfo=UTC),
                source_type=SourceType.EMAIL,
                source_ref="test.txt → message #0",
                quoted_text="We need to fix this ASAP",
            )
        ],
        age_days=14,
        evidence_summary='"We need to fix this ASAP" — Alice (test.txt → message #0)',
    )


class TestFlagsToJson:
    def test_serializes_flags(self):
        flags = {"ProjectA": [_make_flag()]}
        result = _flags_to_json(flags)
        assert "ProjectA" in result
        assert "Test flag" in result
        assert "Alice" in result

    def test_empty_flags(self):
        result = _flags_to_json({})
        assert result == "{}"


class TestGenerateReport:
    def test_calls_llm_with_prompt(self):
        mock_client = MagicMock()
        mock_client.complete.return_value = "# Portfolio Health Report\n\nAll good."

        flags = {"ProjectA": [_make_flag()]}
        result = generate_report(flags, mock_client)

        assert "Portfolio Health Report" in result
        mock_client.complete.assert_called_once()

        # Verify the prompt contains the flags data
        call_args = mock_client.complete.call_args
        user_msg = call_args[1]["messages"][0]["content"]
        assert "ProjectA" in user_msg
        assert "Test flag" in user_msg

    def test_handles_dict_response(self):
        mock_client = MagicMock()
        mock_client.complete.return_value = {"report": "some report"}

        flags = {"ProjectA": [_make_flag()]}
        result = generate_report(flags, mock_client)

        assert "report" in result


class TestBuildReportJson:
    def test_structure(self):
        flags = {"ProjectA": [_make_flag()], "ProjectB": [_make_flag(severity="critical")]}
        report_json = build_report_json(flags, "# Report\n\nContent")

        assert report_json["projects_analyzed"] == 2
        assert report_json["total_flags"] == 2
        assert report_json["critical_flags"] == 1
        assert "ProjectA" in report_json["flags_by_project"]
        assert report_json["report_markdown"] == "# Report\n\nContent"

    def test_empty(self):
        report_json = build_report_json({}, "Empty report")
        assert report_json["total_flags"] == 0
        assert report_json["projects_analyzed"] == 0


class TestSaveReport:
    def test_saves_files(self, tmp_path: Path):
        md_path, json_path = save_report(
            "# Report",
            {"data": "test"},
            tmp_path / "reports",
        )

        assert md_path.exists()
        assert json_path.exists()
        assert md_path.read_text() == "# Report"
        assert '"data"' in json_path.read_text()
        assert md_path.suffix == ".md"
        assert json_path.suffix == ".json"
