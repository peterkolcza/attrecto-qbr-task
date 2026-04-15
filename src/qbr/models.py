"""Core data models for the QBR pipeline."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs this at runtime
from enum import StrEnum

from pydantic import BaseModel, Field

# --- Email parsing models ---


class Message(BaseModel):
    """A single email message within a thread."""

    sender_name: str
    sender_email: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    date: datetime
    subject: str
    body: str
    message_index: int = 0
    is_off_topic: bool = False


class Thread(BaseModel):
    """A parsed email thread (one source file)."""

    source_file: str
    subject: str
    project: str = ""
    messages: list[Message] = Field(default_factory=list)


# --- Provenance / source attribution ---


class SourceType(StrEnum):
    EMAIL = "email"
    MEETING = "meeting"
    DOCUMENT = "document"
    SYSTEM = "system"


class SourceAttribution(BaseModel):
    """Full provenance for a piece of extracted information."""

    person: str
    email: str
    role: str = ""
    timestamp: datetime
    source_type: SourceType = SourceType.EMAIL
    source_ref: str = ""  # e.g. "email5.txt → message #3"
    quoted_text: str = ""


# --- Shared enums ---


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FlagStatus(StrEnum):
    OPEN = "open"
    NEEDS_REVIEW = "needs_review"
    RESOLVED = "resolved"


# --- Extraction pipeline models ---


class ItemType(StrEnum):
    COMMITMENT = "commitment"
    QUESTION = "question"
    RISK = "risk"
    BLOCKER = "blocker"


class ResolutionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class ExtractedItem(BaseModel):
    """An item extracted from an email thread by the LLM."""

    item_type: ItemType
    title: str
    quoted_text: str
    message_index: int
    source: SourceAttribution
    status: ResolutionStatus = ResolutionStatus.OPEN
    resolution_rationale: str = ""
    resolving_message_index: int | None = None
    age_days: int = 0
    severity: Severity = Severity.MEDIUM


# --- Attention flags ---


class FlagType(StrEnum):
    UNRESOLVED_ACTION = "unresolved_action"
    RISK_BLOCKER = "risk_blocker"


class Conflict(BaseModel):
    """When two sources disagree about the same item."""

    description: str
    source_a: SourceAttribution
    source_b: SourceAttribution


class AttentionFlag(BaseModel):
    """A prioritized flag for the Director's attention."""

    flag_type: FlagType
    title: str
    severity: Severity = Severity.MEDIUM
    project: str = ""
    sources: list[SourceAttribution] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    status: FlagStatus = FlagStatus.OPEN
    age_days: int = 0
    evidence_summary: str = ""


# --- Colleague roster ---


class Colleague(BaseModel):
    """A team member from Colleagues.txt."""

    name: str
    email: str
    role: str
    project: str = ""
