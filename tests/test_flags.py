"""Tests for Attention Flag classification and prioritization."""

from __future__ import annotations

from datetime import UTC, datetime

from qbr.flags import (
    aggregate_flags_by_project,
    classify_flags,
    detect_conflicts,
    prioritize_flags,
)
from qbr.models import (
    ExtractedItem,
    FlagType,
    ItemType,
    ResolutionStatus,
    SourceAttribution,
    SourceType,
)


def _make_item(
    item_type: ItemType = ItemType.QUESTION,
    status: ResolutionStatus = ResolutionStatus.OPEN,
    severity: str = "medium",
    age_days: int = 0,
    title: str = "Test item",
    person: str = "Alice",
    email: str = "alice@test.com",
    role: str = "Dev",
) -> ExtractedItem:
    return ExtractedItem(
        item_type=item_type,
        title=title,
        quoted_text="some quote",
        message_index=0,
        source=SourceAttribution(
            person=person,
            email=email,
            role=role,
            timestamp=datetime(2025, 6, 1, tzinfo=UTC),
            source_type=SourceType.EMAIL,
            source_ref="test.txt → message #0",
            quoted_text="some quote",
        ),
        status=status,
        age_days=age_days,
        severity=severity,
    )


class TestClassifyFlags:
    def test_unresolved_old_item_triggers_flag1(self):
        item = _make_item(age_days=10, status=ResolutionStatus.OPEN)
        flags = classify_flags([item], project="TestProject")
        flag1s = [f for f in flags if f.flag_type == FlagType.UNRESOLVED_ACTION]
        assert len(flag1s) == 1
        assert flag1s[0].project == "TestProject"

    def test_unresolved_severe_item_triggers_flag1(self):
        item = _make_item(severity="high", age_days=1, status=ResolutionStatus.OPEN)
        flags = classify_flags([item])
        flag1s = [f for f in flags if f.flag_type == FlagType.UNRESOLVED_ACTION]
        assert len(flag1s) == 1

    def test_resolved_item_no_flag1(self):
        item = _make_item(severity="high", age_days=30, status=ResolutionStatus.RESOLVED)
        flags = classify_flags([item])
        flag1s = [f for f in flags if f.flag_type == FlagType.UNRESOLVED_ACTION]
        assert len(flag1s) == 0

    def test_risk_open_triggers_flag2(self):
        item = _make_item(item_type=ItemType.RISK, status=ResolutionStatus.OPEN)
        flags = classify_flags([item])
        flag2s = [f for f in flags if f.flag_type == FlagType.RISK_BLOCKER]
        assert len(flag2s) == 1

    def test_blocker_open_triggers_flag2(self):
        item = _make_item(item_type=ItemType.BLOCKER, status=ResolutionStatus.OPEN)
        flags = classify_flags([item])
        flag2s = [f for f in flags if f.flag_type == FlagType.RISK_BLOCKER]
        assert len(flag2s) == 1

    def test_risk_resolved_no_flag2(self):
        item = _make_item(item_type=ItemType.RISK, status=ResolutionStatus.RESOLVED)
        flags = classify_flags([item])
        flag2s = [f for f in flags if f.flag_type == FlagType.RISK_BLOCKER]
        assert len(flag2s) == 0

    def test_blocker_can_trigger_both_flags(self):
        """A high-severity open blocker triggers both Flag 1 and Flag 2."""
        item = _make_item(
            item_type=ItemType.BLOCKER,
            status=ResolutionStatus.OPEN,
            severity="critical",
            age_days=20,
        )
        flags = classify_flags([item])
        flag_types = {f.flag_type for f in flags}
        assert FlagType.UNRESOLVED_ACTION in flag_types
        assert FlagType.RISK_BLOCKER in flag_types

    def test_ambiguous_counts_as_unresolved(self):
        item = _make_item(severity="high", status=ResolutionStatus.AMBIGUOUS)
        flags = classify_flags([item])
        flag1s = [f for f in flags if f.flag_type == FlagType.UNRESOLVED_ACTION]
        assert len(flag1s) == 1
        assert flag1s[0].status == "needs_review"

    def test_low_severity_young_item_no_flag(self):
        item = _make_item(severity="low", age_days=2, status=ResolutionStatus.OPEN)
        flags = classify_flags([item])
        assert len(flags) == 0

    def test_evidence_summary_contains_quote(self):
        item = _make_item(severity="high", status=ResolutionStatus.OPEN)
        flags = classify_flags([item])
        assert len(flags) > 0
        assert "some quote" in flags[0].evidence_summary


class TestDetectConflicts:
    def test_no_conflict_with_single_item(self):
        items = [_make_item()]
        conflicts = detect_conflicts(items)
        assert len(conflicts) == 0

    def test_conflict_detected(self):
        resolved = _make_item(
            title="API docs issue",
            status=ResolutionStatus.RESOLVED,
            person="Alice",
            email="alice@test.com",
        )
        still_open = _make_item(
            title="API docs issue",
            status=ResolutionStatus.OPEN,
            person="Bob",
            email="bob@test.com",
        )
        conflicts = detect_conflicts([resolved, still_open])
        assert len(conflicts) == 1
        assert "Alice" in conflicts[0].description
        assert "Bob" in conflicts[0].description


class TestPrioritize:
    def test_critical_before_high(self):
        critical = _make_item(severity="critical")
        high = _make_item(severity="high")
        flags = classify_flags([critical, high])
        # Both should be risks/blockers if typed as such
        items = [
            _make_item(
                item_type=ItemType.BLOCKER, severity="critical", status=ResolutionStatus.OPEN
            ),
            _make_item(
                item_type=ItemType.RISK,
                severity="high",
                status=ResolutionStatus.OPEN,
                title="Other risk",
            ),
        ]
        flags = classify_flags(items)
        prioritized = prioritize_flags(flags)
        assert prioritized[0].severity == "critical"

    def test_top_n_limit(self):
        items = [
            _make_item(
                item_type=ItemType.RISK,
                status=ResolutionStatus.OPEN,
                title=f"Risk {i}",
            )
            for i in range(20)
        ]
        flags = classify_flags(items)
        prioritized = prioritize_flags(flags, top_n=5)
        assert len(prioritized) == 5


class TestAggregateByProject:
    def test_groups_by_project(self):
        items_by_project = {
            "Project A": [
                _make_item(item_type=ItemType.RISK, status=ResolutionStatus.OPEN, title="Risk A"),
            ],
            "Project B": [
                _make_item(
                    item_type=ItemType.BLOCKER, status=ResolutionStatus.OPEN, title="Block B"
                ),
            ],
        }
        result = aggregate_flags_by_project(items_by_project)
        assert "Project A" in result
        assert "Project B" in result
        assert len(result["Project A"]) >= 1
        assert len(result["Project B"]) >= 1

    def test_provenance_preserved(self):
        items_by_project = {
            "TestProject": [
                _make_item(item_type=ItemType.RISK, status=ResolutionStatus.OPEN),
            ],
        }
        result = aggregate_flags_by_project(items_by_project)
        flags = result["TestProject"]
        assert len(flags) > 0
        assert len(flags[0].sources) > 0
        assert flags[0].sources[0].person == "Alice"
