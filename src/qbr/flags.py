"""Attention Flag classification — maps extracted items to the two graded flags.

Flag 1: Unresolved High-Priority Action Items
Flag 2: Emerging Risks / Blockers

Also handles conflict detection when sources disagree.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from qbr.models import (
    AttentionFlag,
    Conflict,
    ExtractedItem,
    FlagType,
    ItemType,
    ResolutionStatus,
)

logger = logging.getLogger(__name__)

# Thresholds
UNRESOLVED_AGE_THRESHOLD_DAYS = 7  # items open longer than this → flag


def classify_flags(
    items: list[ExtractedItem],
    project: str = "",
) -> list[AttentionFlag]:
    """Classify extracted items into Attention Flags.

    Flag 1 — Unresolved High-Priority Action Items:
      status in {open, ambiguous} AND (age_days ≥ threshold OR severity in {high, critical})

    Flag 2 — Emerging Risks / Blockers:
      item_type in {risk, blocker} AND status != resolved
    """
    flags: list[AttentionFlag] = []

    for item in items:
        # Flag 1: Unresolved High-Priority Action Items
        if item.status in (ResolutionStatus.OPEN, ResolutionStatus.AMBIGUOUS):
            is_old = item.age_days >= UNRESOLVED_AGE_THRESHOLD_DAYS
            is_severe = item.severity in ("high", "critical")
            if is_old or is_severe:
                flags.append(
                    AttentionFlag(
                        flag_type=FlagType.UNRESOLVED_ACTION,
                        title=item.title,
                        severity=item.severity,
                        project=project,
                        sources=[item.source],
                        age_days=item.age_days,
                        evidence_summary=f'"{item.quoted_text}" — {item.source.person} '
                        f"({item.source.source_ref})",
                        status="open" if item.status == ResolutionStatus.OPEN else "needs_review",
                    )
                )

        # Flag 2: Emerging Risks / Blockers
        if (
            item.item_type in (ItemType.RISK, ItemType.BLOCKER)
            and item.status != ResolutionStatus.RESOLVED
        ):
            flags.append(
                AttentionFlag(
                    flag_type=FlagType.RISK_BLOCKER,
                    title=item.title,
                    severity=item.severity,
                    project=project,
                    sources=[item.source],
                    age_days=item.age_days,
                    evidence_summary=f'"{item.quoted_text}" — {item.source.person} '
                    f"({item.source.source_ref})",
                    status="open",
                )
            )

    return flags


def detect_conflicts(items: list[ExtractedItem]) -> list[Conflict]:
    """Detect conflicting information from different sources.

    Groups items by normalized title/topic and checks for contradictory statuses
    or claims from different people.
    """
    conflicts: list[Conflict] = []

    # Group items by similar titles
    by_topic: dict[str, list[ExtractedItem]] = defaultdict(list)
    for item in items:
        key = item.title.lower().strip()[:50]  # rough grouping
        by_topic[key].append(item)

    for _topic, group in by_topic.items():
        if len(group) < 2:
            continue
        # Check for status contradictions within the group
        statuses = {i.status for i in group}
        if ResolutionStatus.RESOLVED in statuses and ResolutionStatus.OPEN in statuses:
            resolved_item = next(i for i in group if i.status == ResolutionStatus.RESOLVED)
            open_item = next(i for i in group if i.status == ResolutionStatus.OPEN)
            conflicts.append(
                Conflict(
                    description=f"Conflicting status for '{resolved_item.title}': "
                    f"marked resolved by {resolved_item.source.person} but "
                    f"still open per {open_item.source.person}",
                    source_a=resolved_item.source,
                    source_b=open_item.source,
                )
            )

    return conflicts


def prioritize_flags(
    flags: list[AttentionFlag],
    top_n: int = 10,
) -> list[AttentionFlag]:
    """Sort flags by priority and return the top N.

    Priority order: severity (critical > high > medium > low), then age (older first).
    """
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    sorted_flags = sorted(
        flags,
        key=lambda f: (severity_order.get(f.severity, 4), -f.age_days),
    )

    return sorted_flags[:top_n]


def aggregate_flags_by_project(
    all_items: dict[str, list[ExtractedItem]],
) -> dict[str, list[AttentionFlag]]:
    """Run flag classification for all projects.

    Args:
        all_items: mapping of project_name → list of ExtractedItems

    Returns:
        mapping of project_name → prioritized list of AttentionFlags
    """
    result: dict[str, list[AttentionFlag]] = {}

    for project_name, items in all_items.items():
        flags = classify_flags(items, project=project_name)
        conflicts = detect_conflicts(items)

        # Attach conflicts to relevant flags
        for flag in flags:
            for conflict in conflicts:
                if (
                    conflict.source_a.email == flag.sources[0].email
                    or conflict.source_b.email == flag.sources[0].email
                ):
                    flag.conflicts.append(conflict)

        result[project_name] = prioritize_flags(flags)

        logger.info(
            "Project %s: %d flags (%d conflicts)",
            project_name,
            len(result[project_name]),
            len(conflicts),
        )

    return result
