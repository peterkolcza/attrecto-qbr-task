"""Tests for the email parser — covers all 18 sample emails."""

from __future__ import annotations

from pathlib import Path

import pytest

from qbr.parser import (
    _normalize_subject,
    _parse_date,
    _parse_from_line,
    normalize_email,
    parse_all_emails,
    parse_colleagues,
    parse_email_file,
    parse_thread,
)

SAMPLE_DIR = Path(__file__).parent.parent / "task" / "sample_data"
COLLEAGUES_PATH = SAMPLE_DIR / "Colleagues.txt"


@pytest.fixture
def colleagues():
    return parse_colleagues(COLLEAGUES_PATH)


@pytest.fixture
def all_threads():
    return parse_all_emails(SAMPLE_DIR)


# --- Unit tests for helpers ---


class TestNormalizeEmail:
    def test_basic(self):
        assert normalize_email("nagy.istvan@kisjozsitech.hu") == "nagy.istvan@kisjozsitech.hu"

    def test_diacritics(self):
        assert normalize_email("nagy.istván@kisjozsitech.hu") == "nagy.istvan@kisjozsitech.hu"

    def test_uppercase(self):
        assert normalize_email("Nagy.Istvan@KisJozsiTech.hu") == "nagy.istvan@kisjozsitech.hu"

    def test_whitespace(self):
        assert normalize_email("  nagy.istvan@kisjozsitech.hu  ") == "nagy.istvan@kisjozsitech.hu"


class TestParseFromLine:
    def test_plain_format(self):
        name, email = _parse_from_line("From: Zsuzsa Varga varga.zsuzsa@kisjozsitech.hu")
        assert name == "Zsuzsa Varga"
        assert email == "varga.zsuzsa@kisjozsitech.hu"

    def test_angle_bracket_format(self):
        name, email = _parse_from_line(
            "From: István Nagy <nagy.istván@kisjozsitech.hu>"
        )
        assert name == "István Nagy"
        assert email == "nagy.istván@kisjozsitech.hu"

    def test_parentheses_format(self):
        name, email = _parse_from_line(
            "From: Gábor Nagy (gabor.nagy@kisjozsitech.hu)"
        )
        assert name == "Gábor Nagy"
        assert email == "gabor.nagy@kisjozsitech.hu"


class TestParseDate:
    def test_rfc2822(self):
        dt = _parse_date("Mon, 02 Jun 2025 10:00:00 +0200")
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 2
        assert dt.hour == 10

    def test_abbreviated(self):
        dt = _parse_date("2025.06.09 15:30")
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 9
        assert dt.hour == 15
        assert dt.minute == 30

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("not a date")


class TestNormalizeSubject:
    def test_strips_re(self):
        assert _normalize_subject("Re: Project Phoenix - Login") == "Project Phoenix - Login"

    def test_strips_fwd(self):
        assert _normalize_subject("Fwd: Re: Some topic") == "Some topic"

    def test_strips_fw(self):
        assert _normalize_subject("FW: Something") == "Something"

    def test_no_prefix(self):
        assert _normalize_subject("Project Phoenix - New Login") == "Project Phoenix - New Login"


# --- Colleagues tests ---


class TestParseColleagues:
    def test_count(self, colleagues):
        assert len(colleagues) >= 17  # at least 17 entries in the file

    def test_has_pm(self, colleagues):
        pms = [c for c in colleagues if c.role == "PM"]
        assert len(pms) == 3  # three project managers

    def test_email_formats(self, colleagues):
        emails = {c.email for c in colleagues}
        assert "kovacs.peter@kisjozsitech.hu" in emails
        assert "peter.kovacs@kisjozsitech.hu" in emails


# --- Per-file parsing tests ---


class TestParseEmailFiles:
    """Verify each of the 18 email files parses correctly."""

    @pytest.mark.parametrize(
        "filename,expected_min_messages",
        [
            ("email1.txt", 5),
            ("email2.txt", 5),
            ("email3.txt", 3),
            ("email4.txt", 5),
            ("email5.txt", 5),
            ("email6.txt", 5),
            ("email7.txt", 5),
            ("email8.txt", 5),
            ("email9.txt", 5),
            ("email10.txt", 5),
            ("email11.txt", 1),  # forwarded msg embedded in body, 2nd msg has no standalone From:
            ("email12.txt", 4),
            ("email13.txt", 6),
            ("email14.txt", 5),
            ("email15.txt", 6),
            ("email16.txt", 1),
            ("email17.txt", 5),  # forwarded msg embedded in body
            ("email18.txt", 6),
        ],
    )
    def test_message_count(self, filename: str, expected_min_messages: int):
        path = SAMPLE_DIR / filename
        messages = parse_email_file(path)
        assert (
            len(messages) >= expected_min_messages
        ), f"{filename}: expected ≥{expected_min_messages} messages, got {len(messages)}"

    def test_all_messages_have_dates(self):
        """Every parsed message must have a valid date."""
        for i in range(1, 19):
            path = SAMPLE_DIR / f"email{i}.txt"
            messages = parse_email_file(path)
            for msg in messages:
                assert msg.date is not None, f"email{i}.txt message {msg.message_index} has no date"

    def test_all_messages_have_sender(self):
        """Every parsed message must have a sender name."""
        for i in range(1, 19):
            path = SAMPLE_DIR / f"email{i}.txt"
            messages = parse_email_file(path)
            for msg in messages:
                assert msg.sender_name, f"email{i}.txt message {msg.message_index} has no sender"

    def test_email1_diacritics(self):
        """email1 has both nagy.istvan and nagy.istván — both should normalize."""
        messages = parse_email_file(SAMPLE_DIR / "email1.txt")
        sender_emails = {m.sender_email for m in messages}
        # Both diacritic variants should normalize to same value
        istvan_emails = [e for e in sender_emails if "nagy" in e]
        normalized = {normalize_email(e) for e in istvan_emails}
        assert len(normalized) == 1  # they should all normalize to the same email

    def test_email16_single_message(self):
        """email16.txt has exactly 1 message."""
        messages = parse_email_file(SAMPLE_DIR / "email16.txt")
        assert len(messages) == 1

    def test_chronological_ordering(self):
        """Messages within each thread should be sorted chronologically after parsing."""
        for i in range(1, 19):
            path = SAMPLE_DIR / f"email{i}.txt"
            thread = parse_thread(path)
            if len(thread.messages) > 1:
                dates = [m.date for m in thread.messages]
                assert dates == sorted(dates), (
                    f"email{i}.txt messages not chronologically sorted"
                )


# --- Thread and project attribution tests ---


class TestThreadParsing:
    def test_parse_all_returns_18_threads(self, all_threads):
        assert len(all_threads) == 18

    def test_project_attribution(self, all_threads):
        """Verify project names are assigned."""
        projects = {t.project for t in all_threads}
        # Should have at least Phoenix and DivatKirály
        assert any("Phoenix" in p for p in projects if p), "Phoenix not found"
        assert any("DivatKirály" in p or "Divatkiraly" in p for p in projects if p), (
            "DivatKirály not found"
        )

    def test_project_phoenix_emails(self, all_threads):
        """Emails 1-6 should be attributed to Project Phoenix or related."""
        phoenix_threads = [t for t in all_threads if "Phoenix" in t.project]
        assert len(phoenix_threads) >= 3, f"Expected ≥3 Phoenix threads, got {len(phoenix_threads)}"

    def test_divatkiraly_emails(self, all_threads):
        """DivatKirály emails should be attributed correctly."""
        dk_threads = [
            t for t in all_threads if "DivatKirály" in t.project or "Divatkiraly" in t.project
        ]
        assert len(dk_threads) >= 3, f"Expected ≥3 DivatKirály threads, got {len(dk_threads)}"


# --- Off-topic detection tests ---


class TestOffTopicDetection:
    def test_email2_has_off_topic(self, all_threads):
        """email2 contains lunch/restaurant discussion."""
        thread = next(t for t in all_threads if t.source_file == "email2.txt")
        off_topic_msgs = [m for m in thread.messages if m.is_off_topic]
        assert len(off_topic_msgs) >= 1, "email2 should have off-topic messages (lunch discussion)"

    def test_email8_has_off_topic(self, all_threads):
        """email8 contains birthday surprise discussion."""
        thread = next(t for t in all_threads if t.source_file == "email8.txt")
        off_topic_msgs = [m for m in thread.messages if m.is_off_topic]
        assert len(off_topic_msgs) >= 1, "email8 should have off-topic messages (birthday)"

    def test_email1_no_off_topic(self, all_threads):
        """email1 is a pure technical discussion — no off-topic."""
        thread = next(t for t in all_threads if t.source_file == "email1.txt")
        off_topic_msgs = [m for m in thread.messages if m.is_off_topic]
        assert len(off_topic_msgs) == 0, "email1 should have no off-topic messages"
