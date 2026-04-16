"""Multi-step extraction pipeline — per-thread analysis.

Stage A: Extract items (commitments, questions, risks, blockers) with quotes
Stage B: Track resolution status for each item
Stage C: Compute aging and severity (deterministic, no LLM)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime  # noqa: TC003 — used at runtime for date arithmetic
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from qbr.llm import HAIKU_MODEL, LLMClient
from qbr.models import (
    Colleague,
    ExtractedItem,
    ItemType,
    ResolutionStatus,
    Severity,
    SourceAttribution,
    SourceType,
    Thread,
)
from qbr.parser import normalize_email
from qbr.security import (
    SPOTLIGHTING_PREAMBLE,
    sanitize_email_body,
    verify_quote_in_source,
    wrap_untrusted_content,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# --- Pydantic schemas for LLM structured output ---


class RawExtractedItem(BaseModel):
    """Schema for LLM extraction output (Stage A)."""

    item_type: str
    title: str
    quoted_text: str
    message_index: int
    person: str
    person_email: str


class RawExtractionResult(BaseModel):
    """Wrapper for extraction output."""

    items: list[RawExtractedItem]


class RawResolutionItem(BaseModel):
    """Schema for LLM resolution output (Stage B)."""

    item_type: str
    title: str
    quoted_text: str
    message_index: int
    person: str
    person_email: str
    status: str
    resolution_rationale: str
    resolving_message_index: int | None = None


class RawResolutionResult(BaseModel):
    """Wrapper for resolution output."""

    items: list[RawResolutionItem]


# --- Pipeline functions ---


def _format_thread_for_prompt(thread: Thread) -> str:
    """Format a thread's messages as sanitized, spotlighted content for LLM."""
    parts = []
    for msg in thread.messages:
        if msg.is_off_topic:
            continue  # skip social messages
        sanitized_body = sanitize_email_body(msg.body)
        header = (
            f"[Message {msg.message_index}] "
            f"From: {msg.sender_name} ({msg.sender_email}) | "
            f"Date: {msg.date.isoformat()}"
        )
        parts.append(f"{header}\n{sanitized_body}")

    combined = "\n\n---\n\n".join(parts)
    return wrap_untrusted_content(combined)


def _get_full_thread_text(thread: Thread) -> str:
    """Get the raw text of all messages for grounding checks."""
    return "\n\n".join(msg.body for msg in thread.messages)


def _resolve_role(email: str, colleagues: list[Colleague]) -> str:
    """Look up role for an email address in the colleagues roster."""
    norm = normalize_email(email)
    for c in colleagues:
        if normalize_email(c.email) == norm:
            return c.role
    return ""


def stage_a_extract(
    thread: Thread,
    client: LLMClient,
    model: str = HAIKU_MODEL,
) -> list[dict[str, Any]]:
    """Stage A: Extract items from a thread using quote-first-then-analyze pattern."""
    prompt_template = (PROMPTS_DIR / "extraction.md").read_text(encoding="utf-8")

    thread_content = _format_thread_for_prompt(thread)

    prompt = prompt_template.format(
        spotlighting_preamble=SPOTLIGHTING_PREAMBLE,
        thread_subject=thread.subject,
        source_file=thread.source_file,
        thread_content=thread_content,
    )

    result = client.complete(
        system="You are a precise project analyst. Return valid JSON only.",
        messages=[{"role": "user", "content": prompt}],
        model=model,
        response_schema=RawExtractionResult,
        cache_system=True,
    )

    if isinstance(result, dict):
        items = result.get("items", [])
    else:
        items = json.loads(result).get("items", [])

    logger.info("Stage A: extracted %d items from %s", len(items), thread.source_file)
    return items


def stage_b_resolve(
    thread: Thread,
    items: list[dict[str, Any]],
    client: LLMClient,
    model: str = HAIKU_MODEL,
) -> list[dict[str, Any]]:
    """Stage B: Determine resolution status for each extracted item."""
    if not items:
        return []

    prompt_template = (PROMPTS_DIR / "resolution.md").read_text(encoding="utf-8")

    thread_content = _format_thread_for_prompt(thread)

    prompt = prompt_template.format(
        spotlighting_preamble=SPOTLIGHTING_PREAMBLE,
        thread_subject=thread.subject,
        source_file=thread.source_file,
        thread_content=thread_content,
        items_json=json.dumps(items, indent=2),
    )

    result = client.complete(
        system="You are a precise project analyst. Return valid JSON only.",
        messages=[{"role": "user", "content": prompt}],
        model=model,
        response_schema=RawResolutionResult,
        cache_system=True,
    )

    if isinstance(result, dict):
        resolved_items = result.get("items", [])
    else:
        resolved_items = json.loads(result).get("items", [])

    logger.info(
        "Stage B: %d/%d items resolved in %s",
        sum(1 for i in resolved_items if i.get("status") == "resolved"),
        len(resolved_items),
        thread.source_file,
    )
    return resolved_items


def stage_c_aging_severity(
    items: list[dict[str, Any]],
    thread: Thread,
    colleagues: list[Colleague],
) -> list[ExtractedItem]:
    """Stage C: Compute aging and severity (deterministic, no LLM).

    - age_days: days since item was raised until thread's last message
    - severity: based on role (PM/BA → high), item_type (blocker → critical), and age
    """
    if not thread.messages:
        return []

    last_date = max(m.date for m in thread.messages)
    full_text = _get_full_thread_text(thread)

    result: list[ExtractedItem] = []
    for raw in items:
        # Map raw item_type to enum
        try:
            item_type = ItemType(raw.get("item_type", "question"))
        except ValueError:
            item_type = ItemType.QUESTION

        try:
            status = ResolutionStatus(raw.get("status", "open"))
        except ValueError:
            status = ResolutionStatus.OPEN

        # Get the message date for this item
        msg_idx = raw.get("message_index", 0)
        item_date: datetime = thread.messages[0].date
        for msg in thread.messages:
            if msg.message_index == msg_idx:
                item_date = msg.date
                break

        # Compute age in days
        age_days = (last_date - item_date).days

        # Grounding check: verify the quote exists in the source
        quoted_text = raw.get("quoted_text", "")
        if quoted_text and not verify_quote_in_source(quoted_text, full_text):
            logger.warning(
                "Grounding check failed for item '%s' — quote not found in source, skipping",
                raw.get("title", "?"),
            )
            continue

        # Determine person info
        person_email = raw.get("person_email", "")
        person_name = raw.get("person", "")
        role = _resolve_role(person_email, colleagues) if person_email else ""

        # Severity heuristic
        severity = _compute_severity(item_type, status, role, age_days)

        # Build source attribution
        source = SourceAttribution(
            person=person_name,
            email=normalize_email(person_email) if person_email else "",
            role=role,
            timestamp=item_date,
            source_type=SourceType.EMAIL,
            source_ref=f"{thread.source_file} → message #{msg_idx}",
            quoted_text=quoted_text,
        )

        result.append(
            ExtractedItem(
                item_type=item_type,
                title=raw.get("title", ""),
                quoted_text=quoted_text,
                message_index=msg_idx,
                source=source,
                status=status,
                resolution_rationale=raw.get("resolution_rationale", ""),
                resolving_message_index=raw.get("resolving_message_index"),
                age_days=age_days,
                severity=severity,
            )
        )

    return result


def _compute_severity(
    item_type: ItemType,
    status: ResolutionStatus,
    role: str,
    age_days: int,
) -> Severity:
    """Heuristic severity scoring."""
    if status == ResolutionStatus.RESOLVED:
        return Severity.LOW

    if item_type == ItemType.BLOCKER:
        return Severity.CRITICAL

    if item_type == ItemType.RISK:
        return Severity.HIGH

    if role in ("PM", "BA", "AM") and status != ResolutionStatus.RESOLVED:
        return Severity.HIGH

    if age_days > 14:
        return Severity.HIGH

    if age_days > 7:
        return Severity.MEDIUM

    return Severity.LOW


def run_pipeline_for_thread(
    thread: Thread,
    client: LLMClient,
    colleagues: list[Colleague],
    extraction_model: str = HAIKU_MODEL,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """Run the full 3-stage pipeline for a single thread.

    Returns (items, metrics) where metrics contains per-stage timing and counts.
    """
    import time

    logger.info("Processing thread: %s (%s)", thread.subject, thread.source_file)

    # Stage A: Extract items
    t0 = time.monotonic()
    raw_items = stage_a_extract(thread, client, model=extraction_model)
    extraction_time_ms = int((time.monotonic() - t0) * 1000)

    items_by_type: dict[str, int] = {"commitment": 0, "question": 0, "risk": 0, "blocker": 0}
    for raw in raw_items:
        t = raw.get("item_type", "question")
        if t in items_by_type:
            items_by_type[t] += 1

    # Stage B: Track resolution
    t0 = time.monotonic()
    resolved_items = stage_b_resolve(thread, raw_items, client, model=extraction_model)
    resolution_time_ms = int((time.monotonic() - t0) * 1000)

    resolution_breakdown: dict[str, int] = {"open": 0, "resolved": 0, "ambiguous": 0}
    for r in resolved_items:
        s = r.get("status", "open")
        if s in resolution_breakdown:
            resolution_breakdown[s] += 1

    # Stage C: Aging, severity, grounding
    before_grounding = len(resolved_items)
    items = stage_c_aging_severity(resolved_items, thread, colleagues)
    grounding_drops = before_grounding - len(items)

    severity_breakdown: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in items:
        sev = i.severity.value if hasattr(i.severity, "value") else str(i.severity)
        if sev in severity_breakdown:
            severity_breakdown[sev] += 1

    metrics = {
        "extraction_time_ms": extraction_time_ms,
        "resolution_time_ms": resolution_time_ms,
        "items_by_type": items_by_type,
        "resolution_breakdown": resolution_breakdown,
        "severity_breakdown": severity_breakdown,
        "grounding_drops": grounding_drops,
        "total_time_ms": extraction_time_ms + resolution_time_ms,
    }

    logger.info(
        "Pipeline complete for %s: %d items (%d open)",
        thread.source_file,
        len(items),
        sum(1 for i in items if i.status != ResolutionStatus.RESOLVED),
    )
    return items, metrics
