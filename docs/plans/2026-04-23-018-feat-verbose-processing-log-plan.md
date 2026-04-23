---
title: "feat: Verbose per-email processing log + taller UI panels"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #37"
shipped_in: "PR #38 (commit 37e0032)"
---

# feat: Verbose per-email processing log + taller UI panels

## Overview

Replace the terse two-line per-email progress entries on the job page with a verbose, multi-line trace that names the source file, subject, sender, project, and per-stage metrics (timing + counts) for each thread. Double the height of the Processing Log panel so the new entries are readable without constant scrolling. The goal is to make the dual-LLM quarantine, grounding, and provenance architecture visible to the Attrecto evaluator while the pipeline runs — not just documented after the fact.

## Problem Frame

The pre-existing log emitted only `[5/18] Processing: Project Phoenix — New Login Page...` followed by `→ 5 items (2 open)`. That hides the work the system is doing: which file, which sender, how long extraction took, how many items each stage produced, how many were dropped by grounding. The evaluator watching a live demo cannot map the architecture they read in `Blueprint.md` to what they see on screen. The log panel was also clipped to `max-h-[500px]`, so even the terse history scrolled out of view on an 18-email run.

Origin: GitHub issue #37.

## Requirements Trace

- R1. Per-email log entry includes source file, subject, sender name + email, first-message timestamp, project, and message/off-topic counts. **DONE** — see `_run_analysis` lines emitting "From:", "Project:", and the bracketed header.
- R2. Per-stage summary lines for Stage A (extraction), Stage B (resolution), Stage C (severity) include both counts and timing. **DONE** — `Stage A (Nms): N items (...)`, `Stage B (Nms): ... open/ambiguous/resolved`, `Stage C (severity): ... critical/high/medium/low`.
- R3. Completion line reports total time, items kept, and items dropped by grounding. **DONE** — `✓ Done in N.Ns — N kept, N dropped by grounding`.
- R4. Pipeline returns metrics alongside items so the web layer (and any future caller) can render them. **DONE** — `run_pipeline_for_thread` signature changed from `list[ExtractedItem]` to `tuple[list[ExtractedItem], dict[str, Any]]`.
- R5. Log container in the UI is tall enough to show the new verbose output without immediately scrolling. **DONE** — `max-h-[500px]` → `h-[1000px] max-h-[1000px]` in `job.html`.
- R6. CLI continues to work after the pipeline signature change. **DONE** — `src/qbr/cli.py` updated to unpack the tuple and discard metrics.

## Scope Boundaries

- No log-level coloring (info/warn/error) beyond the existing green-on-black terminal styling — the issue mentioned it but the shipped scope kept the log monochrome to avoid bikeshedding palette choices.
- No structured log export (JSON file, etc.) — log entries remain plain strings appended to `job["progress"]`.
- No matching height bump for the "Pipeline" info panel; only the log container was resized.
- Auto-scroll behavior unchanged — already handled by the existing poll loop.

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/app.py` `_run_analysis` (per-thread loop) — single insertion point for the new log calls. The existing `_log_progress(job, msg)` helper is the only sink.
- `src/qbr/pipeline.py` `run_pipeline_for_thread` — already structured as Stage A → B → C; metrics can be collected at the boundaries without changing pipeline semantics.
- `src/qbr/cli.py` — only other caller of `run_pipeline_for_thread`; needs to be updated in lockstep with the signature change.
- `src/qbr_web/templates/job.html` — single Tailwind utility class change for the panel height.
- `src/qbr/models.py` — `ExtractedItem.severity` is an enum, so the metrics builder uses `i.severity.value if hasattr(i.severity, "value") else str(i.severity)` to be defensive.

## Key Technical Decisions

- **Return metrics as a plain `dict[str, Any]`, not a Pydantic model.** Rationale: metrics are an internal observability payload consumed only by the web logger; a model adds typing overhead with no validation value, and `dict` keeps the diff small.
- **Time stages with `time.monotonic()`.** Rationale: monotonic clock is correct for measuring elapsed durations; wall-clock `time.time()` can jump backwards if NTP corrects.
- **Breaking signature change rather than an out-parameter or sidecar callback.** Rationale: only two callers exist (`cli.py` and `app.py`); updating both in the same commit is cheaper than introducing a metrics protocol or a context object.
- **Compute `grounding_drops` as `before - after` around `stage_c_aging_severity`.** Rationale: that is where colleague-grounding filtering happens; the count is exact and needs no plumbing into Stage C itself.
- **Panel height: `h-[1000px] max-h-[1000px]` (fixed-tall) rather than `min-h`.** Rationale: a fixed height keeps the layout stable as new log lines arrive (the inner div scrolls); a `min-h` would push the page down on every poll.

## Implementation Units

- [x] **Unit 1: Pipeline returns `(items, metrics)`**

  **Goal:** Capture per-stage timing and counts inside `run_pipeline_for_thread` and return them alongside the existing items list.

  **Files:**
  - `src/qbr/pipeline.py`
  - `src/qbr/cli.py` (caller update)
  - `tests/test_pipeline.py` (assertion added)

  **Approach:** Wrap each stage with `time.monotonic()` bookends. Tally `items_by_type` from raw Stage A output, `resolution_breakdown` from Stage B output, `severity_breakdown` from Stage C output, and `grounding_drops` from the length delta around Stage C. Pack into a metrics dict and return as the second tuple element. Update `cli.py` to unpack and discard.

  **Test scenarios:**
  - Pipeline test asserts the returned tuple shape and that the metrics dict contains the documented keys (`extraction_time_ms`, `resolution_time_ms`, `items_by_type`, `resolution_breakdown`, `severity_breakdown`, `grounding_drops`, `total_time_ms`).

- [x] **Unit 2: Web app emits verbose log entries**

  **Goal:** Render the new metrics through `_log_progress` so the UI shows them in real time.

  **Files:**
  - `src/qbr_web/app.py`

  **Approach:** Inside the per-thread loop in `_run_analysis`, before calling the pipeline emit a header block (source file + subject, sender + date, project + message/off-topic counts, "Extracting with {model}..."). After the pipeline returns, emit Stage A / B / C summary lines from the metrics dict, then a completion line with elapsed time, kept count, and grounding drops. Existing error path (`⚠ Error: {e}`) is preserved.

  **Test scenarios:**
  - Existing `tests/test_web.py` job-flow tests still pass (the additional log entries don't break shape assertions).
  - Manual verification: `make web`, run the demo, confirm each email produces a multi-line block with timing and stage counts.

- [x] **Unit 3: Taller log panel**

  **Goal:** Give the verbose log enough vertical space to be useful without forcing constant scrolling.

  **Files:**
  - `src/qbr_web/templates/job.html`

  **Approach:** Replace `max-h-[500px]` on the `#log-container` div with `h-[1000px] max-h-[1000px]`. Inner `overflow-y-auto` already handles scrolling.

  **Verification:** Page renders with a 1000px-tall log panel; new entries scroll inside it; surrounding layout (pipeline info panel, result panel) is unaffected.

## Sources & References

- GitHub issue #37, PR #38, commit `37e0032`
- Affected files:
  - `src/qbr/pipeline.py`
  - `src/qbr/cli.py`
  - `src/qbr_web/app.py`
  - `src/qbr_web/templates/job.html`
  - `tests/test_pipeline.py`
- Adjacent plan: `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md` (later consumes the same per-thread hook point)
