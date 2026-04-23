---
title: "feat: Multi-step extraction pipeline (per-thread)"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #4"
shipped_in: "PR #18 (commit c7bb2ff)"
---

# feat: Multi-step extraction pipeline (per-thread)

## Overview

The analytical heart of the QBR system: a per-thread, three-stage pipeline that turns a parsed email `Thread` into a list of `ExtractedItem` records carrying full `SourceAttribution` provenance. Stages A and B call the LLM (Haiku) with constrained Pydantic-validated output; Stage C is deterministic Python that computes age + severity and runs the output-grounding gate. The pipeline is invoked per-thread by both the CLI (`src/qbr/cli.py`) and the FastAPI app (`src/qbr_web/app.py`), where parallelism is achieved by offloading each thread to a worker via `asyncio.to_thread`.

## Problem Frame

Issue #4 specifies the analytical engine that converts a parsed `Thread` into structured open items with resolution status and source attribution. Without this layer there is no way to surface unresolved blockers (e.g., the email5 CI/CD blocker open 23 days, the email15 payment-gateway blocker open 15 days) as candidates for Attention Flags. The issue calls out two architectural mandates: a "quote-first-then-analyze" extraction pattern (anti-hallucination) and a "dual-LLM" boundary in which extraction calls touch raw email text but synthesis calls only ever see structured records.

## Requirements Trace

- **R1. Stage A — quote-first extraction.** DONE — `stage_a_extract` in `src/qbr/pipeline.py` calls Haiku with `prompts/extraction.md`, which orders the LLM to emit `quoted_text` BEFORE classifying as `commitment | question | risk | blocker`. Output is validated by the `RawExtractionResult` Pydantic schema.
- **R2. Stage B — resolution tracking.** DONE — `stage_b_resolve` performs a separate LLM call per thread, returning `status: open | resolved | ambiguous` plus `resolution_rationale` and `resolving_message_index`. Off-topic messages are dropped earlier in `_format_thread_for_prompt` (the parser's `is_off_topic` flag).
- **R3. Stage C — deterministic aging & severity.** DONE — `stage_c_aging_severity` computes `age_days` from message date to last thread message and applies `_compute_severity` (role-based weighting via `Colleagues.txt`: PM/BA/AM open items → HIGH; blocker → CRITICAL; risk → HIGH; old open → HIGH/MEDIUM by age).
- **R4. Dual-LLM architecture.** DONE — Stages A and B request `response_schema=...`, no tools, structured output only. Synthesis (`src/qbr/report.py`) consumes only the structured `AttentionFlag` records, never raw email text.
- **R5. Source attribution preserved end-to-end.** DONE — every `ExtractedItem` carries a `SourceAttribution(person, email, role, timestamp, source_type=EMAIL, source_ref="emailN.txt → message #M", quoted_text)`. The chain is preserved through `classify_flags` (#5) into the report.
- **R6. Conflict detection.** DONE — `detect_conflicts` (in `src/qbr/flags.py`) groups items by normalized title and flags status contradictions; both versions are preserved with their provenance. (Implementation lives next to the Attention Flag classifier so it consumes the same `ExtractedItem` shape.)
- **R7. Pydantic-validated structured outputs.** DONE — `RawExtractedItem`, `RawExtractionResult`, `RawResolutionItem`, `RawResolutionResult` constrain LLM output. The `LLMClient.complete` signature accepts `response_schema=...` for constrained decoding.
- **R8. Processing events for verbose/debug output.** DONE — every stage logs via the module logger (`Stage A: extracted N items …`, `Stage B: N/M items resolved …`, `Pipeline complete for X: …`); the CLI's `--debug` flag wires this through to stdout, and `_run_analysis` in the web app appends `_log_progress` entries per stage.

## Scope Boundaries

- **Synthesis (Markdown report) is not in this issue** — it lives in `src/qbr/report.py` and consumes the structured output of this pipeline.
- **Attention Flag classification is its own issue (#5)** — `classify_flags` and `aggregate_flags_by_project` were intentionally factored into `src/qbr/flags.py`, not pipeline.
- **No retry/back-off lives in the pipeline itself.** Retry policy is a property of the `LLMClient` provider layer (`src/qbr/llm.py` and the fallback client). The pipeline raises on hard LLM failure and lets the caller (CLI or `_run_analysis`) decide how to log/skip.
- **No persistence between runs.** Each invocation reprocesses all threads — appropriate for the PoC; scaling considerations belong in `Blueprint.md`.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/pipeline.py` — the full pipeline module: `stage_a_extract`, `stage_b_resolve`, `stage_c_aging_severity`, `_compute_severity`, `run_pipeline_for_thread`.
- `src/qbr/prompts/extraction.md` and `src/qbr/prompts/resolution.md` — versioned prompts loaded via `(PROMPTS_DIR / "extraction.md").read_text(...)`. Both interpolate `{spotlighting_preamble}` from `src/qbr/security.py`.
- `src/qbr/security.py` — `sanitize_email_body`, `wrap_untrusted_content`, `SPOTLIGHTING_PREAMBLE`, `verify_quote_in_source`. The pipeline is the sole consumer.
- `src/qbr/models.py` — `ExtractedItem`, `SourceAttribution`, `ItemType`, `ResolutionStatus`, `Severity`, `Colleague`, `Thread` are all Pydantic models with strict types (StrEnum for vocabularies).
- `src/qbr/llm.py` — `LLMClient.complete(...)` accepts `response_schema=` for constrained decoding and `cache_system=True` for prompt caching.
- `src/qbr/parser.py` — `normalize_email` is reused for tolerant role lookup against `Colleagues.txt` (handles diacritic / case mismatches between `From:` lines and email addresses).
- `src/qbr_web/app.py` `_run_analysis` (lines ~487-540) — calls `run_pipeline_for_thread` per thread under `asyncio.to_thread(...)`, achieving cooperative parallelism while keeping the pipeline itself synchronous.

## Key Technical Decisions

- **Quote-first prompt order.** `prompts/extraction.md` instructs the LLM to emit the EXACT `quoted_text` BEFORE the `item_type` classification. This is Anthropic's recommended anti-hallucination technique and pairs with Stage C's grounding gate, which drops any item whose quote does not fuzzy-match the source.
- **Constrained decoding via Pydantic schemas.** Both LLM calls pass `response_schema=RawExtractionResult` / `RawResolutionResult` to `client.complete`, so structurally-malformed output is rejected at the SDK boundary. The pipeline still defends against semantic drift (`try: ItemType(raw.get("item_type", ...))` falls back to `QUESTION` if the LLM emits an unknown vocabulary).
- **Off-topic filtering is upstream of extraction, not after.** `_format_thread_for_prompt` skips messages where `msg.is_off_topic` is true (set by `src/qbr/parser.py`). This avoids paying for tokens on birthday/lunch chatter and keeps the resolution prompt focused. Off-topic messages still exist in the `Thread` for grounding (`_get_full_thread_text`), so a quote that lands in an off-topic message is still considered grounded — important because the parser's classifier is heuristic.
- **Stage C is intentionally LLM-free.** Aging is integer math; severity is a ranked lookup. Keeping this deterministic (a) eliminates a third LLM round-trip per thread, (b) makes severity reproducible across runs, and (c) lets us unit-test the severity rules without mocks (see `TestComputeSeverity` in `tests/test_pipeline.py`).
- **Severity heuristic short-circuits early.** `_compute_severity` returns `LOW` for any resolved item before consulting type/role/age. Order matters: blocker > risk > role(PM/BA/AM) > age — this matches the issue's "PM/BA items > dev items" guidance and ensures a 30-day-old developer question still ranks below a fresh PM commitment.
- **Provenance is built in Stage C, not in extraction.** The LLM emits `person` and `person_email` strings; the pipeline normalizes the email (`normalize_email`), looks up the role from `Colleagues.txt`, and only then constructs `SourceAttribution`. Centralizing this prevents inconsistent role tags across stages.
- **Source-ref format is a stable string.** `f"{thread.source_file} → message #{msg_idx}"` is read by humans in the rendered report and asserted in tests. The arrow is intentional — it is unambiguous when grepping logs.
- **Per-stage timing + breakdown metrics.** `run_pipeline_for_thread` returns `(items, metrics)`; metrics expose `extraction_time_ms`, `resolution_time_ms`, per-type counts, and `grounding_drops`. These power the live dashboard and the `--debug` CLI trace without requiring callers to instrument the LLM client themselves.
- **Parallelism lives in the caller, not the pipeline.** `run_pipeline_for_thread` is plain sync code; the web app gets parallelism by wrapping each thread in `asyncio.to_thread(...)`. Keeping the pipeline sync makes the unit tests trivial (no event-loop fixtures) and lets the CLI run sequentially for deterministic debug output.

## Implementation Units

- [x] **Unit 1: Stage A — quote-first extraction**

  **Goal:** Call Haiku with the spotlighted thread content and a quote-first prompt; return a list of raw item dicts validated against `RawExtractionResult`.

  **Files:**
  - `src/qbr/pipeline.py` — `stage_a_extract`, `_format_thread_for_prompt`, `RawExtractedItem`, `RawExtractionResult`
  - `src/qbr/prompts/extraction.md` — versioned prompt with `{spotlighting_preamble}`, `{thread_subject}`, `{source_file}`, `{thread_content}` placeholders

  **Approach:** Read the prompt template, format with sanitized + spotlighted thread content, call `client.complete(..., response_schema=RawExtractionResult, cache_system=True)`, return `result["items"]`. Logs `Stage A: extracted N items from <source_file>`.

  **Test scenarios:**
  - `tests/test_pipeline.py::TestStageAExtract::test_extraction` — happy path with mocked LLM returning a single commitment.
  - `tests/test_pipeline.py::TestStageAExtract::test_empty_response` — LLM returns no items → empty list, no crash.
  - `tests/test_pipeline.py::TestFormatThread::test_format_includes_messages` — thread content survives the spotlight wrapping.
  - `tests/test_pipeline.py::TestFormatThread::test_format_skips_off_topic` — `is_off_topic` messages are not sent to the LLM.

- [x] **Unit 2: Stage B — resolution tracking**

  **Goal:** Per-thread LLM call that decides for each Stage A item whether the same thread later resolved it.

  **Files:**
  - `src/qbr/pipeline.py` — `stage_b_resolve`, `RawResolutionItem`, `RawResolutionResult`
  - `src/qbr/prompts/resolution.md` — prompt that gets back `items_json` from Stage A plus the same spotlighted thread content

  **Approach:** Skip the call entirely when Stage A returned nothing. Otherwise format the prompt with `items_json=json.dumps(items, indent=2)` plus the spotlighted thread, call the LLM with `response_schema=RawResolutionResult`. Logs the resolved/total ratio.

  **Test scenarios:**
  - `tests/test_pipeline.py::TestStageBResolve::test_resolution_tracking` — items come back with `status` filled in.
  - `tests/test_pipeline.py::TestStageBResolve::test_empty_items` — early return when Stage A produced nothing (no LLM call).

- [x] **Unit 3: Stage C — deterministic aging, severity, and grounding gate**

  **Goal:** Convert raw LLM dicts into validated `ExtractedItem` records with `age_days`, `severity`, `SourceAttribution`. Drop any item whose `quoted_text` cannot be grounded in the source thread.

  **Files:**
  - `src/qbr/pipeline.py` — `stage_c_aging_severity`, `_compute_severity`, `_resolve_role`, `_get_full_thread_text`
  - `src/qbr/security.py` — `verify_quote_in_source` (called per item)
  - `src/qbr/models.py` — `ExtractedItem`, `SourceAttribution`, `ItemType`, `ResolutionStatus`, `Severity`

  **Approach:** For each raw dict: coerce `item_type` and `status` to enums (with defensive fallbacks), look up the message date, compute `age_days = (last_msg_date - item_date).days`, run the grounding check (skip with warning if it fails), resolve role from `Colleagues.txt` via `normalize_email`, compute severity, build the `SourceAttribution`, append the `ExtractedItem`.

  **Test scenarios:**
  - `tests/test_pipeline.py::TestStageCAgingSeverity::test_aging_and_severity` — full happy-path conversion preserves provenance.
  - `tests/test_pipeline.py::TestStageCAgingSeverity::test_grounding_filter` — fabricated quote is dropped.
  - `tests/test_pipeline.py::TestStageCAgingSeverity::test_empty_thread` — no messages → no items, no exception.
  - `tests/test_pipeline.py::TestComputeSeverity::test_blocker_is_critical`, `test_risk_is_high`, `test_pm_open_is_high`, `test_old_open_is_high`, `test_resolved_is_low`, `test_young_dev_question_is_low`, `test_medium_age` — exhaustive severity rule coverage without mocks.

- [x] **Unit 4: `run_pipeline_for_thread` orchestrator**

  **Goal:** Single entry point for callers (CLI + web) that runs A → B → C, captures per-stage timing + breakdown metrics, and returns `(items, metrics)`.

  **Files:**
  - `src/qbr/pipeline.py` — `run_pipeline_for_thread`
  - `src/qbr/cli.py` — single-process caller (sequential, deterministic)
  - `src/qbr_web/app.py` — async caller that wraps the call in `asyncio.to_thread(...)` for parallelism across threads

  **Approach:** Time each stage with `time.monotonic()`. Build per-type counts from raw items, resolution counts from Stage B output, severity counts from Stage C output, and `grounding_drops = before_grounding - len(items)`. Log `Pipeline complete for X: N items (M open)`.

  **Test scenarios:**
  - `tests/test_pipeline.py::TestRunPipelineForThread::test_end_to_end` — A + B + C wired together with a mocked LLM and a sample thread / colleagues fixture.

- [x] **Unit 5: Security layer integration (input sanitization + spotlighting + grounding)**

  **Goal:** Hook the dual-LLM-quarantine boundary into every LLM call this pipeline makes. Issue #6 ships in the same PR (#18) and is documented in its own retro plan; this unit records what the pipeline consumes.

  **Files:**
  - `src/qbr/pipeline.py` — `_format_thread_for_prompt` chains `sanitize_email_body` → `wrap_untrusted_content`; `stage_c_aging_severity` calls `verify_quote_in_source` per item.
  - `src/qbr/prompts/extraction.md`, `src/qbr/prompts/resolution.md` — embed `{spotlighting_preamble}` so the LLM sees the security instruction inside the same context window as the untrusted content.

  **Approach:** Every email body gets sanitized (HTML strip, role-tag neutralization) before being wrapped in the `<untrusted_email_content>` delimiter. Every extracted quote is fuzzy-matched against the un-sanitized full thread text (`_get_full_thread_text`) so we ground against ground truth, not the LLM's possibly-mangled view.

  **Test scenarios:**
  - `tests/test_pipeline.py::TestVerifyQuoteInSource::test_exact_match`, `test_fuzzy_match`, `test_no_match`, `test_empty_quote` — grounding gate behavior.
  - `tests/test_pipeline.py::TestStageCAgingSeverity::test_grounding_filter` — fabricated quotes are dropped at Stage C.

## System-Wide Impact

- **Callers:** `src/qbr/cli.py` (sequential per-thread loop) and `src/qbr_web/app.py::_run_analysis` (parallel via `asyncio.to_thread`). Both consume `(items, metrics)` and merge `items` into a per-project bucket for downstream classification.
- **Downstream:** `src/qbr/flags.py::aggregate_flags_by_project` and `src/qbr/report.py` both depend on the `ExtractedItem` shape and the populated `SourceAttribution`.
- **Cross-cutting security boundary:** the pipeline is the ONLY module that touches raw email text inside an LLM prompt. Synthesis prompts (in `report.py`) only consume validated `AttentionFlag` records — preserving the dual-LLM quarantine called out in issue #4.
- **Observability:** the `metrics` dict feeds into the live dashboard's per-thread progress events and the `--debug` CLI flag's stage-by-stage trace. The dashboard's "active project" flash leans on the per-thread granularity introduced by this orchestrator.
- **Cost surface:** every thread is now a 2-LLM-call shape (A + B). Caching the system prompt (`cache_system=True`) keeps the cost of the second call sub-linear within a single run. The `Blueprint.md` cost-management section relies on this.

## Sources & References

- Issue: [#4](https://github.com/peterkolcza/attrecto-qbr-task/issues/4)
- PR: [#18](https://github.com/peterkolcza/attrecto-qbr-task/pull/18) (commit `c7bb2ff`) — shipped together with the security layer (issue #6).
- Related plans: `docs/plans/2026-04-23-005-feat-attention-flag-classification-plan.md`, `docs/plans/2026-04-23-006-feat-prompt-injection-defense-plan.md`.
- Code:
  - `src/qbr/pipeline.py`
  - `src/qbr/security.py`
  - `src/qbr/models.py`
  - `src/qbr/prompts/extraction.md`, `src/qbr/prompts/resolution.md`
  - `tests/test_pipeline.py`
