"""Email parser — parses raw .txt email files into structured Thread/Message models.

Handles two distinct formats found in the sample data:
- RFC 2822 (emails 1-6): `From: Name email@domain` or `From: Name <email@domain>`
- Abbreviated (emails 7-18): `From: Name (email@domain)`

Also parses Colleagues.txt for role/project attribution.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from pathlib import Path

from unidecode import unidecode

from qbr.models import Colleague, Message, Thread

# --- From: line patterns ---
# Variant 1: Name email@domain (no brackets)
_FROM_PLAIN = re.compile(r"^From:\s+(.+?)\s+([\w.]+@[\w.]+\.\w+)\s*$")
# Variant 2: Name <email@domain>
_FROM_ANGLE = re.compile(r"^From:\s+(.+?)\s+<(.+?)>\s*$")
# Variant 3: Name (email@domain)
_FROM_PAREN = re.compile(r"^From:\s+(.+?)\s+\((.+?)\)\s*$")

# --- Date: line patterns ---
# RFC 2822: Mon, 02 Jun 2025 10:00:00 +0200
_DATE_RFC2822 = re.compile(
    r"^Date:\s+\w{3},\s+\d{2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+[+-]\d{4}\s*$"
)
# Abbreviated: 2025.06.09 15:30
_DATE_ABBREV = re.compile(r"^Date:\s+(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})\s*$")

# --- Social/off-topic detection ---
_OFF_TOPIC_PATTERNS = re.compile(
    r"\b(birthday|szülinap|surprise|meglepetés|lunch|ebéd|cake|torta|restaurant|étterem|"
    r"gift|ajándék|pizza|mexican|mexikói|party|buli)\b",
    re.IGNORECASE,
)

# --- Subject normalization ---
_SUBJECT_PREFIX = re.compile(r"^(?:Re|Fwd|FW|Fw):\s*", re.IGNORECASE)

# --- Project detection from subject ---
_PROJECT_PATTERNS = [
    (re.compile(r"Project\s+Phoenix", re.IGNORECASE), "Project Phoenix"),
    (re.compile(r"DivatKir[áa]ly", re.IGNORECASE), "DivatKirály"),
]


def normalize_email(email: str) -> str:
    """Normalize email for matching: lowercase + strip diacritics."""
    return unidecode(email.strip().lower())


def parse_colleagues(path: str | Path) -> list[Colleague]:
    """Parse Colleagues.txt into a list of Colleague models."""
    path = Path(path)
    colleagues: list[Colleague] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("Characters"):
            continue
        # Format: "Role: Name (email)"
        match = re.match(r"^(.+?):\s+(.+?)\s+\((.+?)\)\s*$", line)
        if match:
            role_raw, name, email = match.groups()
            # Extract just the role label (e.g. "Project Manager (PM)" → "PM")
            role_match = re.search(r"\((\w+)\)", role_raw)
            role = role_match.group(1) if role_match else role_raw.strip()
            colleagues.append(Colleague(name=name.strip(), email=email.strip(), role=role))
    return colleagues


def _detect_project_from_colleagues(participants: set[str], colleagues: list[Colleague]) -> str:
    """Determine project based on which colleagues are in the email."""
    # Build a mapping: normalized_email → list of projects
    email_to_projects: dict[str, list[str]] = {}
    for c in colleagues:
        norm = normalize_email(c.email)
        if c.project:
            email_to_projects.setdefault(norm, []).append(c.project)

    # Count project votes from participants
    votes: dict[str, int] = {}
    for addr in participants:
        norm = normalize_email(addr)
        for proj in email_to_projects.get(norm, []):
            votes[proj] = votes.get(proj, 0) + 1

    if votes:
        return max(votes, key=lambda p: votes[p])
    return ""


def _assign_projects_to_colleagues(colleagues: list[Colleague]) -> list[Colleague]:
    """Assign project names based on roster position in Colleagues.txt.

    The roster has 3 groups (Phoenix, unnamed/Omicron, DivatKirály) identifiable
    by their PM entries and position in the file.
    """
    projects = ["Project Phoenix", "Project Omicron", "DivatKirály"]
    pm_count = 0
    current_project = ""

    result = []
    for c in colleagues:
        if c.role == "PM":
            if pm_count < len(projects):
                current_project = projects[pm_count]
            pm_count += 1
        result.append(Colleague(name=c.name, email=c.email, role=c.role, project=current_project))
    return result


def _parse_from_line(line: str) -> tuple[str, str]:
    """Parse a From: line into (name, email). Returns ('', '') if unparseable."""
    for pattern in (_FROM_ANGLE, _FROM_PAREN, _FROM_PLAIN):
        m = pattern.match(line)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    # Fallback: just a name with no email
    m = re.match(r"^From:\s+(.+)$", line)
    if m:
        return m.group(1).strip(), ""
    return "", ""


def _parse_date(date_str: str) -> datetime:
    """Parse a Date: value string into a datetime."""
    date_str = date_str.strip()

    # Try RFC 2822 format
    try:
        return datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        pass

    # Try abbreviated format: 2025.06.09 15:30
    try:
        dt = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
        return dt.replace(tzinfo=UTC)
    except ValueError:
        pass

    raise ValueError(f"Cannot parse date: {date_str!r}")


def _parse_recipients(value: str) -> list[str]:
    """Extract email addresses from a To: or Cc: value.

    Handles formats like:
    - 'Name email@domain, Name2 email2@domain'
    - 'Name (email@domain), Name2 (email2@domain)'
    - 'Name <email@domain>, Name2 <email2@domain>'
    - 'Name, Name2' (no emails — returns empty list)
    """
    emails = re.findall(r"[\w.]+@[\w.]+\.\w+", value)
    return [normalize_email(e) for e in emails]


def _is_off_topic(body: str) -> bool:
    """Detect social/off-topic messages by keyword matching."""
    return bool(_OFF_TOPIC_PATTERNS.search(body))


def _normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd:/FW: prefixes and normalize whitespace."""
    s = subject.strip()
    while _SUBJECT_PREFIX.match(s):
        s = _SUBJECT_PREFIX.sub("", s, count=1).strip()
    return s


def _detect_project_from_subject(subject: str) -> str:
    """Try to detect project from subject line patterns."""
    for pattern, project_name in _PROJECT_PATTERNS:
        if pattern.search(subject):
            return project_name
    return ""


def _split_message_blocks(text: str) -> list[str]:
    """Split raw email text into individual message blocks.

    Two formats exist:
    - RFC 2822 (emails 1-6): blocks start with "From:" after blank lines
    - Abbreviated (emails 7-18): blocks start with "Subject:" after blank lines

    We split on blank line(s) followed by a header keyword (From:|Subject:|Date:)
    where the block contains at least From: and Date: within its first few lines.
    """
    # Split on blank line followed by a recognized header start
    # This handles both From:-first and Subject:-first formats
    blocks = re.split(r"\n\n+(?=(?:From:|Subject:|Date:)\s)", text.strip())
    blocks = [b.strip() for b in blocks if b.strip()]

    # Filter: a valid message block must contain both From: and Date: lines
    valid_blocks = []
    for block in blocks:
        lines_head = block[:500]  # check first 500 chars for headers
        has_from = "From:" in lines_head
        has_date = "Date:" in lines_head
        if has_from and has_date:
            valid_blocks.append(block)
        elif valid_blocks:
            # Append to previous block (e.g., forwarded message content)
            valid_blocks[-1] += "\n\n" + block
        # else: discard orphan block before first valid message

    return valid_blocks


def parse_email_file(path: str | Path) -> list[Message]:
    """Parse a single email .txt file into a list of Message objects."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    blocks = _split_message_blocks(text)

    messages: list[Message] = []
    for idx, block in enumerate(blocks):
        lines = block.splitlines()

        sender_name = ""
        sender_email = ""
        to_list: list[str] = []
        cc_list: list[str] = []
        date: datetime | None = None
        subject = ""
        body_lines: list[str] = []

        in_body = False
        for line in lines:
            if in_body:
                body_lines.append(line)
                continue

            if line.startswith("From:"):
                sender_name, sender_email = _parse_from_line(line)
            elif line.startswith("To:"):
                to_list = _parse_recipients(line[3:])
            elif line.startswith("Cc:"):
                cc_list = _parse_recipients(line[3:])
            elif line.startswith("Date:"):
                date_value = line[5:].strip()
                with contextlib.suppress(ValueError):
                    date = _parse_date(date_value)
            elif line.startswith("Subject:"):
                subject = line[8:].strip()
            elif line == "":
                # Empty line after headers = start of body
                in_body = True
            else:
                # Continuation line in headers or body start without blank line
                if not any(
                    line.startswith(h) for h in ("From:", "To:", "Cc:", "Date:", "Subject:")
                ):
                    in_body = True
                    body_lines.append(line)

        body = "\n".join(body_lines).strip()

        if date is None:
            # Skip blocks without a parseable date (e.g., forwarded message headers)
            continue

        messages.append(
            Message(
                sender_name=sender_name,
                sender_email=normalize_email(sender_email) if sender_email else "",
                to=to_list,
                cc=cc_list,
                date=date,
                subject=subject,
                body=body,
                message_index=idx,
                is_off_topic=_is_off_topic(body),
            )
        )

    return messages


def parse_thread(
    path: str | Path,
    colleagues: list[Colleague] | None = None,
) -> Thread:
    """Parse an email file into a Thread with project attribution."""
    path = Path(path)
    messages = parse_email_file(path)

    if not messages:
        return Thread(source_file=path.name, subject="", messages=[])

    # Use the first message's subject (normalized) as the thread subject
    raw_subject = messages[0].subject
    thread_subject = _normalize_subject(raw_subject)

    # Detect project from subject
    project = _detect_project_from_subject(raw_subject)

    # If not detected from subject, try participant-based detection
    if not project and colleagues:
        participants: set[str] = set()
        for m in messages:
            if m.sender_email:
                participants.add(m.sender_email)
            participants.update(m.to)
            participants.update(m.cc)
        project = _detect_project_from_colleagues(participants, colleagues)

    # Sort messages chronologically
    messages.sort(key=lambda m: m.date)

    return Thread(
        source_file=path.name,
        subject=thread_subject,
        project=project,
        messages=messages,
    )


def parse_all_emails(
    directory: str | Path,
    colleagues_path: str | Path | None = None,
) -> list[Thread]:
    """Parse all email*.txt files in a directory into Threads."""
    directory = Path(directory)

    # Load colleagues roster if available
    colleagues: list[Colleague] | None = None
    if colleagues_path is None:
        default_colleagues = directory / "Colleagues.txt"
        if default_colleagues.exists():
            colleagues_path = default_colleagues

    if colleagues_path:
        raw_colleagues = parse_colleagues(colleagues_path)
        colleagues = _assign_projects_to_colleagues(raw_colleagues)

    # Parse all email files
    email_files = sorted(directory.glob("email*.txt"), key=lambda p: _email_sort_key(p.name))
    threads = [parse_thread(f, colleagues) for f in email_files]

    return threads


def _email_sort_key(filename: str) -> int:
    """Sort email files numerically: email1.txt, email2.txt, ..., email18.txt."""
    m = re.search(r"(\d+)", filename)
    return int(m.group(1)) if m else 0
