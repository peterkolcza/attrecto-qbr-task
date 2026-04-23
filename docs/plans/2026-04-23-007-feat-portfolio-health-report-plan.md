---
title: "feat: Portfolio Health Report generator with source attribution"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #7"
shipped_in: "PR #20 (commit 84e53c1)"
---

# feat: Portfolio Health Report generator with source attribution

## Overview

Final synthesis step of the QBR pipeline. Takes the prioritized `dict[str, list[AttentionFlag]]` from the flag classifier (issue #5) and asks Sonnet 4.6 to produce a structured Markdown Portfolio Health Report with full source attribution on every claim. Also emits a parallel JSON document so the web dashboard (Phase 2) can render the same data without re-parsing Markdown.

The module deliberately does only three things — serialize flags to a stable JSON shape for the prompt, call the LLM with a versioned synthesis prompt, and persist both `portfolio_<timestamp>.md` and `portfolio_<timestamp>.json`. Aggregation, severity ordering, and conflict detection happen upstream in `src/qbr/flags.py`.

## Problem Frame

Issue #7 calls for "the final LLM synthesis step: takes prioritized flags with full provenance and generates a structured Portfolio Health Report." The Director needs a single document that says where to focus attention; everything in that document must be traceable back to a specific email quote. Without this step the pipeline stops at a list of flags — useful internally but not the deliverable the brief asks for.

## Requirements Trace

- R1. DONE — Sonnet 4.6 used for synthesis (1 call per run). `generate_report` defaults to `SONNET_MODEL` and accepts the prioritized `flags_by_project` mapping (`src/qbr/report.py:61`).
- R2. DONE — Report structure (Executive Summary, Per-project, Cross-project, Conflicts, Recommended Actions) enforced via `src/qbr/prompts/synthesis.md`.
- R3. DONE — Every claim is traceable: `_flags_to_json` serializes each flag's `sources[]` (person, email, role, ISO timestamp, `source_ref`, `quoted_text`) and `conflicts[]` so the LLM has the full provenance chain in its context.
- R4. DONE — Versioned prompt lives in the prompts directory (now `src/qbr/prompts/synthesis.md` — moved from top-level `prompts/` during the package restructure).
- R5. DONE — Dual output: `save_report` writes both `portfolio_<timestamp>.md` and `portfolio_<timestamp>.json` (`src/qbr/report.py:117`); the JSON includes `model_dump(mode='json')` of every flag for the dashboard.

## Scope Boundaries

- No flag selection / re-ranking — that responsibility belongs to `aggregate_flags_by_project` in `src/qbr/flags.py`. The report module trusts the order it receives.
- No retry / cost tracking logic in this module — `LLMClient` and `UsageTracker` handle that.
- No HTML rendering — the web layer reads the JSON output and renders separately (`src/qbr_web/`).
- No streaming output — single blocking `client.complete` call. Acceptable for one-LLM-call-per-run workload.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/llm.py` — `LLMClient.complete(system, messages, model, temperature, max_tokens)` is the abstraction; `SONNET_MODEL` constant is the default for synthesis-tier calls.
- `src/qbr/models.py` — `AttentionFlag`, `SourceAttribution`, `Conflict` Pydantic models. `model_dump(mode='json')` already handles datetime → ISO string for the JSON output.
- `src/qbr/flags.py` `aggregate_flags_by_project` — returns the already-prioritized mapping the report consumes.
- `src/qbr/prompts/extraction.md`, `resolution.md` — sibling prompt files; `synthesis.md` follows the same versioned-file pattern (read at call time, no template engine).

## Key Technical Decisions

- **JSON-shaped prompt input, not free text.** `_flags_to_json` produces a deterministic JSON structure for the user message. Rationale: the synthesis prompt asks the model to cite sources verbatim, so giving it structured fields (rather than a flattened narrative) keeps the quote → person → email chain intact.
- **`temperature=0.1`, `max_tokens=8192`.** Synthesis is a structured-writing task with no creative requirement. Low temperature reduces phrasing drift across runs; 8K output ceiling is more than enough for a 3-project portfolio with ~10 flags.
- **Defensive dict-response handling.** If the underlying client returns a dict (some Ollama configurations do this with JSON-mode), `generate_report` re-serializes to a string so callers always get Markdown text. Cheaper than enforcing a contract on every provider.
- **Timestamp-suffixed filenames.** Each run writes a fresh pair (`portfolio_YYYYMMDD_HHMMSS.md/.json`) — never overwrites. Keeps a local audit trail without needing a database.

## Implementation Units

- [x] **Unit 1: Flag JSON serializer**

  **Goal:** Deterministic JSON dump of `flags_by_project` shaped for the synthesis prompt — preserves every field the model needs to cite sources accurately.

  **Files:**
  - `src/qbr/report.py` (`_flags_to_json`)

  **Approach:** Iterate projects → flags, emit `{flag_type, title, severity, age_days, status, evidence_summary, sources[], conflicts[]}`. Use `ensure_ascii=False` to keep Hungarian diacritics intact in the prompt. ISO-format every timestamp explicitly so the output is JSON-safe even though the source is a Pydantic `datetime`.

  **Test scenarios:**
  - `tests/test_report.py::TestFlagsToJson::test_serializes_flags` — project name, flag title, and source person all appear in the output.
  - `tests/test_report.py::TestFlagsToJson::test_empty_flags` — empty input → `"{}"`.

- [x] **Unit 2: LLM synthesis call**

  **Goal:** Single Sonnet 4.6 call combining the versioned prompt and the serialized flags. Returns Markdown text.

  **Files:**
  - `src/qbr/report.py` (`generate_report`)
  - `src/qbr/prompts/synthesis.md` (versioned prompt)

  **Approach:** Read `synthesis.md`, `.format(flags_json=...)`, send via `client.complete` with the consultant system prompt at `temperature=0.1`. Defensive: if the client returns a dict, JSON-encode it back to a string.

  **Test scenarios:**
  - `tests/test_report.py::TestGenerateReport::test_calls_llm_with_prompt` — asserts the LLM is called once and the user message contains the project name and flag title.
  - `tests/test_report.py::TestGenerateReport::test_handles_dict_response` — dict response is coerced to a string containing the dict's keys.

- [x] **Unit 3: Dashboard JSON envelope**

  **Goal:** Produce the JSON document the web layer consumes — flag counts, critical count, per-project flag dump, and the rendered Markdown embedded for convenience.

  **Files:**
  - `src/qbr/report.py` (`build_report_json`)

  **Approach:** Compute totals; serialize every flag via `model_dump(mode='json')`; include `generated_at` (UTC ISO), `projects_analyzed`, `total_flags`, `critical_flags`, `flags_by_project`, and `report_markdown`.

  **Test scenarios:**
  - `tests/test_report.py::TestBuildReportJson::test_structure` — counts and keys are correct for a 2-project / 2-flag fixture (one critical).
  - `tests/test_report.py::TestBuildReportJson::test_empty` — empty input → all counts zero.

- [x] **Unit 4: Dual-output persistence**

  **Goal:** Write `portfolio_<timestamp>.md` and `portfolio_<timestamp>.json` to the configured output directory, creating it if missing.

  **Files:**
  - `src/qbr/report.py` (`save_report`)

  **Approach:** `Path(output_dir).mkdir(parents=True, exist_ok=True)`; UTC timestamp format `%Y%m%d_%H%M%S`; UTF-8 with `ensure_ascii=False` for the JSON. Returns the `(md_path, json_path)` tuple so the CLI can echo both.

  **Test scenarios:**
  - `tests/test_report.py::TestSaveReport::test_saves_files` — both files exist after the call, suffixes are correct, contents round-trip.

## Sources & References

- GitHub issue: #7
- Pull request / commit: PR #20, commit `84e53c1`
- Related code: `src/qbr/report.py`, `src/qbr/prompts/synthesis.md`, `src/qbr/flags.py`, `src/qbr/llm.py`, `src/qbr/models.py`
- Tests: `tests/test_report.py`
