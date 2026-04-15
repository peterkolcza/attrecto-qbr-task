"""Tests for the multi-step extraction pipeline — all LLM calls mocked."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from qbr.models import Colleague, ItemType, Message, ResolutionStatus, Thread
from qbr.pipeline import (
    _compute_severity,
    _format_thread_for_prompt,
    run_pipeline_for_thread,
    stage_a_extract,
    stage_b_resolve,
    stage_c_aging_severity,
)
from qbr.security import verify_quote_in_source


@pytest.fixture
def sample_thread() -> Thread:
    """A minimal thread for testing."""
    return Thread(
        source_file="test_email.txt",
        subject="Test Thread",
        project="Test Project",
        messages=[
            Message(
                sender_name="Alice",
                sender_email="alice@test.com",
                to=["bob@test.com"],
                cc=[],
                date=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
                subject="Test Thread",
                body="We need to fix the login bug before the release. Can someone handle this?",
                message_index=0,
            ),
            Message(
                sender_name="Bob",
                sender_email="bob@test.com",
                to=["alice@test.com"],
                cc=[],
                date=datetime(2025, 6, 1, 11, 0, tzinfo=UTC),
                subject="Re: Test Thread",
                body="I'll take a look at the login bug today.",
                message_index=1,
            ),
            Message(
                sender_name="Charlie",
                sender_email="charlie@test.com",
                to=["alice@test.com", "bob@test.com"],
                cc=[],
                date=datetime(2025, 6, 15, 9, 0, tzinfo=UTC),
                subject="Re: Test Thread",
                body="The payment gateway integration is blocked. The API docs are wrong.",
                message_index=2,
            ),
        ],
    )


@pytest.fixture
def sample_colleagues() -> list[Colleague]:
    return [
        Colleague(name="Alice", email="alice@test.com", role="PM", project="Test Project"),
        Colleague(name="Bob", email="bob@test.com", role="Dev", project="Test Project"),
        Colleague(name="Charlie", email="charlie@test.com", role="Dev", project="Test Project"),
    ]


# --- Thread formatting tests ---


class TestFormatThread:
    def test_format_includes_messages(self, sample_thread):
        formatted = _format_thread_for_prompt(sample_thread)
        assert "untrusted_email_content" in formatted
        assert "Alice" in formatted
        assert "login bug" in formatted

    def test_format_skips_off_topic(self, sample_thread):
        sample_thread.messages[1].is_off_topic = True
        formatted = _format_thread_for_prompt(sample_thread)
        assert "Bob" not in formatted  # skipped
        assert "Alice" in formatted  # kept


# --- Grounding verification tests ---


class TestGrounding:
    def test_exact_match(self):
        assert verify_quote_in_source(
            "fix the login bug", "We need to fix the login bug before the release."
        )

    def test_fuzzy_match(self):
        assert verify_quote_in_source(
            "fix the login bug before release",
            "We need to fix the login bug before the release.",
        )

    def test_no_match(self):
        assert not verify_quote_in_source(
            "the server crashed yesterday",
            "We need to fix the login bug before the release.",
        )

    def test_empty_quote(self):
        assert not verify_quote_in_source("", "some source text")


# --- Severity computation tests ---


class TestSeverity:
    def test_blocker_is_critical(self):
        assert _compute_severity(ItemType.BLOCKER, ResolutionStatus.OPEN, "Dev", 1) == "critical"

    def test_risk_is_high(self):
        assert _compute_severity(ItemType.RISK, ResolutionStatus.OPEN, "Dev", 1) == "high"

    def test_pm_open_is_high(self):
        assert _compute_severity(ItemType.QUESTION, ResolutionStatus.OPEN, "PM", 1) == "high"

    def test_old_open_is_high(self):
        assert _compute_severity(ItemType.QUESTION, ResolutionStatus.OPEN, "Dev", 20) == "high"

    def test_resolved_is_low(self):
        assert _compute_severity(ItemType.BLOCKER, ResolutionStatus.RESOLVED, "PM", 30) == "low"

    def test_young_dev_question_is_low(self):
        assert _compute_severity(ItemType.QUESTION, ResolutionStatus.OPEN, "Dev", 3) == "low"

    def test_medium_age(self):
        assert _compute_severity(ItemType.QUESTION, ResolutionStatus.OPEN, "Dev", 10) == "medium"


# --- Stage A tests (mocked LLM) ---


class TestStageA:
    def test_extraction(self, sample_thread):
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "items": [
                {
                    "item_type": "question",
                    "title": "Who handles the login bug?",
                    "quoted_text": "Can someone handle this?",
                    "message_index": 0,
                    "person": "Alice",
                    "person_email": "alice@test.com",
                },
                {
                    "item_type": "blocker",
                    "title": "Payment gateway API docs incorrect",
                    "quoted_text": "The API docs are wrong.",
                    "message_index": 2,
                    "person": "Charlie",
                    "person_email": "charlie@test.com",
                },
            ]
        }

        items = stage_a_extract(sample_thread, mock_client)
        assert len(items) == 2
        assert items[0]["item_type"] == "question"
        assert items[1]["item_type"] == "blocker"
        mock_client.complete.assert_called_once()

    def test_empty_response(self, sample_thread):
        mock_client = MagicMock()
        mock_client.complete.return_value = {"items": []}
        items = stage_a_extract(sample_thread, mock_client)
        assert items == []


# --- Stage B tests (mocked LLM) ---


class TestStageB:
    def test_resolution_tracking(self, sample_thread):
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "items": [
                {
                    "item_type": "question",
                    "title": "Who handles the login bug?",
                    "quoted_text": "Can someone handle this?",
                    "message_index": 0,
                    "person": "Alice",
                    "person_email": "alice@test.com",
                    "status": "resolved",
                    "resolution_rationale": "Bob committed to fixing it.",
                    "resolving_message_index": 1,
                },
                {
                    "item_type": "blocker",
                    "title": "Payment gateway docs wrong",
                    "quoted_text": "The API docs are wrong.",
                    "message_index": 2,
                    "person": "Charlie",
                    "person_email": "charlie@test.com",
                    "status": "open",
                    "resolution_rationale": "No response in the thread.",
                    "resolving_message_index": None,
                },
            ]
        }

        input_items = [{"item_type": "question"}, {"item_type": "blocker"}]
        result = stage_b_resolve(sample_thread, input_items, mock_client)
        assert len(result) == 2
        assert result[0]["status"] == "resolved"
        assert result[1]["status"] == "open"

    def test_empty_items(self, sample_thread):
        mock_client = MagicMock()
        result = stage_b_resolve(sample_thread, [], mock_client)
        assert result == []
        mock_client.complete.assert_not_called()


# --- Stage C tests (deterministic, no LLM) ---


class TestStageC:
    def test_aging_and_severity(self, sample_thread, sample_colleagues):
        raw_items = [
            {
                "item_type": "question",
                "title": "Who handles the login bug?",
                "quoted_text": "Can someone handle this?",
                "message_index": 0,
                "person": "Alice",
                "person_email": "alice@test.com",
                "status": "open",
                "resolution_rationale": "",
                "resolving_message_index": None,
            },
            {
                "item_type": "blocker",
                "title": "API docs wrong",
                "quoted_text": "The API docs are wrong.",
                "message_index": 2,
                "person": "Charlie",
                "person_email": "charlie@test.com",
                "status": "open",
                "resolution_rationale": "",
                "resolving_message_index": None,
            },
        ]

        result = stage_c_aging_severity(raw_items, sample_thread, sample_colleagues)

        assert len(result) == 2

        # First item: question from PM (Alice), raised Jun 1 10:00, last msg Jun 15 09:00
        # timedelta = 13 days 23 hours → .days = 13
        assert result[0].age_days == 13
        assert result[0].severity == "high"  # PM role → high
        assert result[0].source.role == "PM"
        assert result[0].source.source_ref == "test_email.txt → message #0"

        # Second item: blocker from Dev (Charlie), raised Jun 15, last msg Jun 15 → 0 days
        assert result[1].age_days == 0
        assert result[1].severity == "critical"  # blocker → critical
        assert result[1].item_type == ItemType.BLOCKER

    def test_grounding_filter(self, sample_thread, sample_colleagues):
        """Items with fabricated quotes should be filtered out."""
        raw_items = [
            {
                "item_type": "risk",
                "title": "Hallucinated risk",
                "quoted_text": "The database is completely corrupted and all data is lost",
                "message_index": 0,
                "person": "Alice",
                "person_email": "alice@test.com",
                "status": "open",
                "resolution_rationale": "",
                "resolving_message_index": None,
            },
        ]

        result = stage_c_aging_severity(raw_items, sample_thread, sample_colleagues)
        assert len(result) == 0  # filtered out by grounding check

    def test_empty_thread(self, sample_colleagues):
        empty_thread = Thread(source_file="empty.txt", subject="Empty", messages=[])
        result = stage_c_aging_severity([], empty_thread, sample_colleagues)
        assert result == []


# --- Full pipeline test (mocked LLM) ---


class TestFullPipeline:
    def test_end_to_end(self, sample_thread, sample_colleagues):
        mock_client = MagicMock()

        # Stage A response
        extraction_result = {
            "items": [
                {
                    "item_type": "question",
                    "title": "Who handles login bug?",
                    "quoted_text": "Can someone handle this?",
                    "message_index": 0,
                    "person": "Alice",
                    "person_email": "alice@test.com",
                },
            ]
        }

        # Stage B response
        resolution_result = {
            "items": [
                {
                    "item_type": "question",
                    "title": "Who handles login bug?",
                    "quoted_text": "Can someone handle this?",
                    "message_index": 0,
                    "person": "Alice",
                    "person_email": "alice@test.com",
                    "status": "resolved",
                    "resolution_rationale": "Bob said he'd look at it.",
                    "resolving_message_index": 1,
                },
            ]
        }

        mock_client.complete.side_effect = [extraction_result, resolution_result]

        items = run_pipeline_for_thread(sample_thread, mock_client, sample_colleagues)

        assert len(items) == 1
        assert items[0].status == ResolutionStatus.RESOLVED
        assert items[0].severity == "low"  # resolved → low
        assert items[0].source.person == "Alice"
        assert items[0].source.role == "PM"

        # LLM was called exactly twice (Stage A + Stage B)
        assert mock_client.complete.call_count == 2
