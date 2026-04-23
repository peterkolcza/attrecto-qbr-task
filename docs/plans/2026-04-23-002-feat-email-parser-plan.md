---
title: "feat: Email parser & thread grouping with dual date formats and off-topic detection"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #2"
shipped_in: "PR #16 (commit 9462f59)"
---

# feat: Email parser & thread grouping with dual date formats and off-topic detection

## Overview

Convert the 18 raw `.txt` email files in `task/sample_data/` into structured `Thread`/`Message` Pydantic objects so the rest of the pipeline can reason over canonical data instead of regex-walking text. The parser handles the two date formats present in the corpus, three `From:` line variants, diacritic-normalised email identity, social/off-topic message tagging, project attribution via subject prefix and `Colleagues.txt` voting, and per-thread chronological sorting. 47 tests cover all 18 files plus the helper functions.

## Problem Frame

From the issue body:

> Parse the 18 raw email txt files into structured Thread/Message Pydantic models, group by project.

The corpus is deliberately messy — RFC 2822 headers in some files, an abbreviated `YYYY.MM.DD HH:MM` format in others, three different `From:` line variants, contaminated threads where birthdays and lunch invites land mid-conversation, and two distinct people whose display names collide ("Péter Kovács" exists with two different mailboxes). Every downstream stage (LLM extraction, flag classification, report) needs trustworthy structure, so the parser has to be tolerant in input and strict in output.

## Requirements Trace

- R1. Input `task/sample_data/*.txt`, one file per thread, blank-line-separated message blocks — DONE.
- R2. Output `Thread(subject, project, messages[])` and `Message(from, to, cc, date, body)` Pydantic models — DONE (models declared in `src/qbr/models.py` from #1).
- R3. Diacritic-tolerant identity (`nagy.istván` ≡ `nagy.istvan`) and optional angle-bracket addresses — DONE via `unidecode`-based `normalize_email` and three-branch `_parse_from_line` (plain / `<…>` / `(…)`).
- R4. Dual date format parsing: RFC 2822 (Phoenix 1–6) and abbreviated `YYYY.MM.DD HH:MM` (Omicron/DivatKirály 7–18) — DONE.
- R5. Project attribution via subject prefix (`Project Phoenix`, `DivatKirály`) plus `Colleagues.txt` PM-mapping fallback — DONE (`_detect_project_from_subject` then participant voting via `_detect_project_from_colleagues`).
- R6. Email address is the primary key for person identity (two distinct `Péter Kovács`) — DONE.
- R7. Off-topic detection (lunch, birthdays, etc.) sets `is_off_topic` on `Message` — DONE via `_is_off_topic`.
- R8. Edge cases handled: `email16.txt` (single message), `email4.txt` (non-chronological order in source) — DONE; output is sorted chronologically.
- R9. Test suite proves 18 files parse, 3 project groups form, dual date formats are recognised — DONE (47 tests in `tests/test_parser.py`).

## Scope Boundaries

- No quoted-reply stripping — message body is captured verbatim, including any in-reply quoting. The LLM extraction stage handles deduplication.
- No attachment handling — corpus has none.
- No timezone normalisation beyond what `email.utils.parsedate_to_datetime` returns; the abbreviated format is parsed as naive local time. Downstream age-of-flag math uses `age_days`, which is robust to this.
- Off-topic detection is keyword-based, not LLM-based — cheap heuristic appropriate for a parser; the LLM pipeline can ignore flagged messages.
- No streaming/lazy parsing — corpus is small enough to hold every thread in memory.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/parser.py` — all parsing logic lives here.
- `src/qbr/models.py` — `Message` (with `is_off_topic: bool = False` from #1), `Thread`, `Colleague`.
- `task/sample_data/email1.txt` … `email18.txt` — the 18 raw fixtures.
- `task/sample_data/Colleagues.txt` — roster used for participant-vote project attribution.
- `tests/test_parser.py` — 47 tests, organised into `TestNormalizeEmail`, `TestParseFromLine`, `TestParseDate`, `TestNormalizeSubject`, `TestParseColleagues`, `TestParseEmailFiles`, `TestThreadParsing`, `TestOffTopicDetection`.

## Key Technical Decisions

- **`unidecode`-normalised lowercase email as identity key.** Rationale: `nagy.istván@…` and `nagy.istvan@…` are the same person in the fictional company (the diacritic was lost during transliteration to the email address). Comparing display names is unsafe — two distinct people share "Péter Kovács" but have different mailboxes (`kovacs.peter@` vs `peter.kovacs@`). Email is the primary key; names are display-only.
- **Three-branch `_parse_from_line`** handles the three patterns observed in the corpus: plain `Name email@domain`, `Name <email@domain>` (only `email1.txt` line 23 in the original spec), and `Name (email@domain)`. Returning `(name, normalized_email)` everywhere keeps callers branch-free.
- **Dual date parser tries RFC 2822 first, then the `YYYY.MM.DD HH:MM` regex.** RFC 2822 uses `email.utils.parsedate_to_datetime` (the standard library does the heavy lifting); the abbreviated format is a single `re.compile`. Rationale: trying RFC 2822 first matches the order the corpus was authored in and avoids accidentally matching the abbreviated format on mis-quoted RFC dates. Unparseable dates raise — better to fail loud than silently mis-attribute timing data the flag engine reasons about.
- **Project attribution layered, subject-prefix first.** `_detect_project_from_subject` matches on `Project Phoenix`, `DivatKirály`, `Omicron`. If the subject doesn't carry a prefix, `_detect_project_from_colleagues` votes by participant overlap with the roster (which is loaded once and assigned-to-project via `_assign_projects_to_colleagues`). Rationale: subject prefix is the cheapest, most precise signal; participant voting is the fallback that handles the mid-thread reply where the prefix has been stripped.
- **Subject normalisation strips `Re:`/`Fwd:`/`FW:` prefixes before threading.** Rationale: a thread is one conversation regardless of forwarding etiquette; threading on the un-prefixed subject means edge cases like email4 still group correctly.
- **Off-topic detection is a small keyword list, not an LLM call.** Rationale: the parser must run without secrets and stay deterministic for tests. LLM-grade nuance is not required — `is_off_topic` is a hint to downstream stages, not a hard filter.
- **Per-thread chronological sort happens in `parse_thread`**, not at message-block read time. Rationale: source files (notably `email4.txt`) sometimes list messages out of order; the parser normalises this so the LLM and flag engine can trust message ordering.
- **`parse_email_file` returns `list[Message]`; `parse_thread` aggregates into one `Thread`; `parse_all_emails` returns `list[Thread]` over the full corpus.** Sorted by filename via `_email_sort_key` so `email2` precedes `email10`.

## Implementation Units

- [x] **Unit 1: Identity, date, and subject helpers**

  **Goal:** Provide the diacritic-tolerant email key, the dual date parser, the subject normaliser, and the three-branch `From:` line parser that the rest of the parser builds on.

  **Files:**
  - `src/qbr/parser.py`

  **Approach:**
  - `normalize_email(email)` lowercases and `unidecode`s, stripping whitespace.
  - `_parse_from_line(line)` regexes the three observed shapes, returns `(name, normalized_email)`.
  - `_parse_date(date_str)` tries `email.utils.parsedate_to_datetime`, falls back to a `YYYY.MM.DD HH:MM` regex; raises on no match.
  - `_normalize_subject(subject)` strips a leading `Re:`/`Fwd:`/`FW:` (case-insensitive, repeated).

  **Test scenarios:**
  - `tests/test_parser.py::TestNormalizeEmail::test_basic / test_diacritics / test_uppercase / test_whitespace`
  - `tests/test_parser.py::TestParseFromLine::test_plain_format / test_angle_bracket_format / test_parentheses_format`
  - `tests/test_parser.py::TestParseDate::test_rfc2822 / test_abbreviated / test_invalid_raises`
  - `tests/test_parser.py::TestNormalizeSubject::test_strips_re / test_strips_fwd / test_strips_fw / test_no_prefix`

- [x] **Unit 2: `Colleagues.txt` roster and project assignment**

  **Goal:** Read the roster file into `Colleague` models and assign each colleague to a project, so the participant-vote attribution path has data.

  **Files:**
  - `src/qbr/parser.py`
  - `task/sample_data/Colleagues.txt` (consumed)

  **Approach:**
  - `parse_colleagues(path)` reads the roster, returns `list[Colleague]`.
  - `_assign_projects_to_colleagues(colleagues)` maps each colleague to a project based on the roster's project sections.

  **Test scenarios:**
  - `tests/test_parser.py::TestParseColleagues::test_count` — total roster size matches the file.
  - `tests/test_parser.py::TestParseColleagues::test_has_pm` — each project has at least one PM.
  - `tests/test_parser.py::TestParseColleagues::test_email_formats` — every colleague's email passes `normalize_email`.

- [x] **Unit 3: Single-file message parsing + off-topic detection**

  **Goal:** Convert one `.txt` file into a chronologically ordered list of `Message` objects with `is_off_topic` populated.

  **Files:**
  - `src/qbr/parser.py`

  **Approach:**
  - `_split_message_blocks(text)` slices the file on blank-line + `From:` boundaries (handling both date formats).
  - `parse_email_file(path)` walks blocks, parses headers via `_parse_from_line`/`_parse_date`/`_parse_recipients`, captures the body, and tags `is_off_topic=_is_off_topic(body)`.
  - `_is_off_topic(body)` matches a small keyword set (birthday, lunch, etc.).

  **Test scenarios:**
  - `tests/test_parser.py::TestParseEmailFiles::test_message_count` — parametrised across all 18 files; asserts each parses to at least the expected number of messages.
  - `tests/test_parser.py::TestParseEmailFiles::test_all_messages_have_dates / test_all_messages_have_sender` — every parsed message carries header data.
  - `tests/test_parser.py::TestParseEmailFiles::test_email1_diacritics` — `nagy.istván` ≡ `nagy.istvan` after normalisation.
  - `tests/test_parser.py::TestParseEmailFiles::test_email16_single_message` — single-message thread parses cleanly.
  - `tests/test_parser.py::TestParseEmailFiles::test_chronological_ordering` — `email4.txt`'s out-of-order source is sorted on output.
  - `tests/test_parser.py::TestOffTopicDetection::test_email2_has_off_topic / test_email8_has_off_topic / test_email1_no_off_topic`.

- [x] **Unit 4: Thread aggregation + project attribution**

  **Goal:** Bundle messages per thread, assign a project, and expose `parse_all_emails` over the whole corpus.

  **Files:**
  - `src/qbr/parser.py`

  **Approach:**
  - `parse_thread(path, colleagues)` reads messages, derives `subject` from the first message (normalised), runs `_detect_project_from_subject` then falls back to `_detect_project_from_colleagues` voting over the participant set.
  - `parse_all_emails(directory, colleagues)` walks `*.txt`, sorts via `_email_sort_key`, returns `list[Thread]`.

  **Test scenarios:**
  - `tests/test_parser.py::TestThreadParsing::test_parse_all_returns_18_threads`
  - `tests/test_parser.py::TestThreadParsing::test_project_attribution` — exactly three project groups.
  - `tests/test_parser.py::TestThreadParsing::test_project_phoenix_emails` — emails 1–6 attribute to Phoenix.
  - `tests/test_parser.py::TestThreadParsing::test_divatkiraly_emails` — emails 13–18 attribute to DivatKirály.

## Sources & References

- Issue: <https://github.com/peterkolcza/attrecto-qbr-task/issues/2>
- PR: <https://github.com/peterkolcza/attrecto-qbr-task/pull/16>
