---
title: "feat: Attention Flag classification & prioritization"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #5"
shipped_in: "PR #19 (commit bcf019c)"
---

# feat: Attention Flag classification & prioritization

## Overview

Turns the structured `ExtractedItem` records emitted by the per-thread extraction pipeline (#4) into the two graded **Attention Flags** required by the task brief: **Unresolved High-Priority Action Items** and **Emerging Risks / Blockers**. Adds conflict detection so contradictory statements about the same topic are surfaced (with both sources preserved), priority sorting, and a portfolio-level aggregator that returns one prioritized flag list per project. Every flag carries the full `SourceAttribution` chain so the Director can audit any claim back to the originating quote and email file.

## Problem Frame

The task brief grades "1–2 well-defined Attention Flags." Issue #5 fixes the two flag types and the rules that map `ExtractedItem` → `AttentionFlag`, with three mandatory robustness properties: every flag carries its provenance chain, contradictions are flagged rather than silently merged, and the portfolio view is prioritized so the Director sees the most acute items first. Without this layer, the pipeline only produces a flat per-thread item list and the report cannot tell the Director where to focus their attention.

## Requirements Trace

- **R1. Flag 1 — Unresolved High-Priority Action Items.** DONE — `classify_flags` emits `FlagType.UNRESOLVED_ACTION` when `status in {OPEN, AMBIGUOUS}` AND (`age_days >= UNRESOLVED_AGE_THRESHOLD_DAYS` OR `severity in {HIGH, CRITICAL}`).
- **R2. Flag 2 — Emerging Risks / Blockers.** DONE — `classify_flags` emits `FlagType.RISK_BLOCKER` when `item_type in {RISK, BLOCKER}` AND `status != RESOLVED`. A blocker can produce both flags (covered by `test_blocker_can_trigger_both_flags`).
- **R3. Portfolio-level prioritization, top-N flags reach the report.** DONE — `prioritize_flags` sorts by `severity` (CRITICAL → HIGH → MEDIUM → LOW) then by `-age_days` (older first) and slices to `top_n=10`.
- **R4. Every flag carries full provenance.** DONE — `AttentionFlag.sources: list[SourceAttribution]` is populated from the originating `ExtractedItem.source`. `evidence_summary` interpolates the quote, the person, and the source ref.
- **R5. Conflict handling.** DONE — `detect_conflicts` groups items by normalized title and flags status contradictions (RESOLVED + OPEN for the same topic). Both sources are preserved on the resulting `Conflict`. `aggregate_flags_by_project` then attaches the relevant `Conflict` records to the matching flags.
- **R6. Aggregation per project.** DONE — `aggregate_flags_by_project(all_items: dict[str, list[ExtractedItem]])` returns `dict[str, list[AttentionFlag]]`, prioritized per project.
- **R7. Tests use deterministic fixtures with mocked LLM.** DONE — every test in `tests/test_flags.py` constructs `ExtractedItem` records directly; no LLM call is made (the pipeline already mocks LLM in `tests/test_pipeline.py`).

## Scope Boundaries

- **Severity is computed upstream**, not here. `_compute_severity` in `src/qbr/pipeline.py` is the single source of truth; this module only consumes `item.severity`.
- **Resolution status comes from the pipeline.** This module does not re-evaluate whether something is open or resolved.
- **No cross-project conflict detection.** Conflicts are scoped per project bucket; the same title across two projects is not flagged.
- **No fuzzy title matching.** Conflict grouping uses `title.lower().strip()[:50]` — intentionally rough; it is conservative (false negatives over false positives so unrelated items are not falsely linked).
- **No persistence / no acknowledgement state.** Each run produces a fresh prioritized list.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/flags.py` — entire module: `classify_flags`, `detect_conflicts`, `prioritize_flags`, `aggregate_flags_by_project`, `UNRESOLVED_AGE_THRESHOLD_DAYS = 7`.
- `src/qbr/models.py`:
  - `AttentionFlag` (sources + conflicts as default-empty lists, `status: FlagStatus = OPEN`)
  - `FlagType` (StrEnum: `UNRESOLVED_ACTION`, `RISK_BLOCKER`)
  - `FlagStatus` (StrEnum: `OPEN`, `NEEDS_REVIEW`, `RESOLVED`)
  - `Conflict(description, source_a, source_b)`
  - `ExtractedItem` and `SourceAttribution` (consumed)
- `src/qbr/pipeline.py::_compute_severity` — the upstream severity rule this module relies on.
- `src/qbr/report.py` — primary downstream consumer; it renders the prioritized flag lists into the Markdown QBR report.
- `src/qbr_web/app.py::_run_analysis` — calls `classify_flags` per-thread for live dashboard counts and `aggregate_flags_by_project` once at end of run for the canonical sorted view.

## Key Technical Decisions

- **Two distinct flag types, not one merged "attention" type.** `FlagType.UNRESOLVED_ACTION` and `FlagType.RISK_BLOCKER` map cleanly to the two flag types named in the task brief and let the report group them under separate headings. A single item can produce both (a blocker is both an action item and a risk-blocker) — `test_blocker_can_trigger_both_flags` documents that this is intentional.
- **Threshold is a module constant, not a config knob.** `UNRESOLVED_AGE_THRESHOLD_DAYS = 7` is named and exported; production tuning would lift it to env, but for the PoC the constant keeps the rule auditable in code.
- **Severity-first sort, age as tie-breaker.** `prioritize_flags` uses `(severity_order, -age_days)`; CRITICAL items always rank above HIGH items regardless of age, which matches "critical → act now" framing. Within a severity bucket, older items rise — they have had the longest opportunity to be resolved and weren't.
- **`top_n=10` is a per-project cap, not a global cap.** Each project gets up to 10 flags. The report renderer further trims if needed, but at this layer we keep enough headroom for the Director to see all critical items even on a busy project.
- **Conflict grouping uses a string prefix, not embeddings.** `title.lower().strip()[:50]` is a pragmatic rough-grouping rule that catches "SSO confirmed" vs "SSO removed from scope" without dragging in an embedding dependency. Issue acknowledged: borderline duplicates with different wording will be missed — Blueprint.md notes embeddings as a Phase-2 hardening.
- **Conflict records attach to flags, not to items.** `aggregate_flags_by_project` walks each conflict and appends it to any flag whose `sources[0].email` matches either side of the conflict. This makes the report easier to render: each flag locally knows about its disputes.
- **`FlagStatus.NEEDS_REVIEW` for ambiguous items.** When the upstream resolution status is `AMBIGUOUS`, the flag is marked `NEEDS_REVIEW` rather than `OPEN`. This separates "we know it's open" from "the LLM couldn't tell" in the rendered report and on the live dashboard.
- **Per-thread classification is cheap and side-effect-free**, so the web app calls `classify_flags` per thread (for live count updates) and `aggregate_flags_by_project` once at end of run (for the canonical, conflict-decorated, prioritized view). `detect_conflicts` only runs at end of run because it needs the full item set.

## Implementation Units

- [x] **Unit 1: `classify_flags` — map items to the two flag types**

  **Goal:** Pure function over `list[ExtractedItem]` that emits `AttentionFlag` records, one per matching rule (an item can produce both types).

  **Files:**
  - `src/qbr/flags.py` — `classify_flags`, `UNRESOLVED_AGE_THRESHOLD_DAYS`
  - `src/qbr/models.py` — `AttentionFlag`, `FlagType`, `FlagStatus`

  **Approach:** Iterate items; emit Flag 1 when status is OPEN/AMBIGUOUS AND (old OR severe); emit Flag 2 when type is RISK/BLOCKER AND not resolved. Map `AMBIGUOUS` → `FlagStatus.NEEDS_REVIEW`. Build `evidence_summary` as `f'"{quoted_text}" — {person} ({source_ref})'` so the report does not have to re-format provenance per render.

  **Test scenarios:**
  - `tests/test_flags.py::TestClassifyFlags::test_unresolved_old_item_triggers_flag1`
  - `tests/test_flags.py::TestClassifyFlags::test_unresolved_severe_item_triggers_flag1`
  - `tests/test_flags.py::TestClassifyFlags::test_resolved_item_no_flag1`
  - `tests/test_flags.py::TestClassifyFlags::test_risk_open_triggers_flag2`
  - `tests/test_flags.py::TestClassifyFlags::test_blocker_open_triggers_flag2`
  - `tests/test_flags.py::TestClassifyFlags::test_risk_resolved_no_flag2`
  - `tests/test_flags.py::TestClassifyFlags::test_blocker_can_trigger_both_flags`
  - `tests/test_flags.py::TestClassifyFlags::test_ambiguous_counts_as_unresolved`
  - `tests/test_flags.py::TestClassifyFlags::test_low_severity_young_item_no_flag`
  - `tests/test_flags.py::TestClassifyFlags::test_evidence_summary_contains_quote`

- [x] **Unit 2: `detect_conflicts` — surface contradictions**

  **Goal:** Group items by normalized title; if a group contains both a RESOLVED and an OPEN item, emit a `Conflict` linking the two sources.

  **Files:**
  - `src/qbr/flags.py` — `detect_conflicts`
  - `src/qbr/models.py` — `Conflict`

  **Approach:** Bucket by `item.title.lower().strip()[:50]`. Skip groups of size 1. For each conflicting group, take the first RESOLVED and first OPEN item, build a `Conflict(description, source_a=resolved, source_b=open)`. Description includes both names so the report can render it as-is.

  **Test scenarios:**
  - `tests/test_flags.py::TestDetectConflicts::test_no_conflict_with_single_item`
  - `tests/test_flags.py::TestDetectConflicts::test_conflict_detected`

- [x] **Unit 3: `prioritize_flags` — severity then age sort, top-N**

  **Goal:** Stable sort by `(severity_order, -age_days)`; truncate to `top_n=10` by default.

  **Files:**
  - `src/qbr/flags.py` — `prioritize_flags`

  **Approach:** Build a static `severity_order = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}`; sort and slice. Pure function.

  **Test scenarios:**
  - `tests/test_flags.py::TestPrioritizeFlags::test_critical_before_high`
  - `tests/test_flags.py::TestPrioritizeFlags::test_top_n_limit`

- [x] **Unit 4: `aggregate_flags_by_project` — portfolio aggregator**

  **Goal:** Single end-of-run call that runs classification + conflict detection + prioritization per project and decorates each flag with its relevant conflicts.

  **Files:**
  - `src/qbr/flags.py` — `aggregate_flags_by_project`
  - Callers: `src/qbr/cli.py`, `src/qbr_web/app.py::_run_analysis`, `src/qbr/report.py`

  **Approach:** For each `(project_name, items)` pair: `flags = classify_flags(items, project=project_name)`, `conflicts = detect_conflicts(items)`, attach each conflict to any flag whose `sources[0].email` matches either side, then `prioritize_flags(flags)`. Log per-project flag and conflict counts.

  **Test scenarios:**
  - `tests/test_flags.py::TestAggregateFlagsByProject::test_groups_by_project`
  - `tests/test_flags.py::TestAggregateFlagsByProject::test_provenance_preserved`

## Sources & References

- Issue: [#5](https://github.com/peterkolcza/attrecto-qbr-task/issues/5)
- PR: [#19](https://github.com/peterkolcza/attrecto-qbr-task/pull/19) (commit `bcf019c`)
- Related plans:
  - `docs/plans/2026-04-23-004-feat-extraction-pipeline-plan.md` — produces the `ExtractedItem` records this module consumes.
  - `docs/plans/2026-04-23-006-feat-prompt-injection-defense-plan.md` — provides the grounding gate that ensures every `quoted_text` reaching this module is anchored in the source.
- Code: `src/qbr/flags.py`, `src/qbr/models.py`, `tests/test_flags.py`.
