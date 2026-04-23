---
title: "feat: Live project dashboard precursor (superseded by #45)"
type: feat
status: superseded
date: 2026-04-23
retro: true
origin: "GitHub issue #44"
shipped_in: "Superseded by #45 — see docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md"
superseded_by: "#45"
---

# feat: Live project dashboard precursor (superseded by #45)

## Overview

This plan exists for completeness of the issue/plan correspondence. Issue #44 was the first attempt to specify the "live project dashboard" feature: project cards on `/` updating in real time during email processing, drill-down per project, and persistent project health that survives a server restart. It was closed without a separate implementation because issue #45 was opened to clarify the same feature with a tighter, more honest scope (in-memory state only, no persistence) and is what actually shipped.

## Problem Frame

The task brief in `task/AI_Developer.pdf` describes the system's job as telling the Director "exactly where to focus their limited attention." Before the live dashboard work, the post-run experience was a static "Pending analysis" card on `/` plus a Markdown report — the dashboard itself communicated nothing about portfolio health.

Issue #44 framed the response correctly but added one requirement that turned out to be out of scope for a PoC graded on mindset and structure: persisting `project_state` to a JSON file on disk so health survives a server restart. The persistence requirement pulled in concerns (file format, eviction, race conditions on concurrent writes, backwards compatibility) that distract from the core "where to focus" UX value. Issue #45 was opened to narrow the scope to in-memory state only, with everything else preserved.

## Requirements Trace

All requirements from #44 were either re-specified under #45 or explicitly dropped:

- R1. Live project cards with health indicator, flag counts, last-updated timestamp, in-progress badge. **Deferred to #45** (re-specified verbatim, shipped in commit `24bbe70` / PR #46).
- R2. Drill-down `/projects/{name}` page listing flags with evidence and a back-link to the report. **Deferred to #45**.
- R3. 3-second polling of a JSON state endpoint while a job is active; per-card flash for the project currently being processed. **Deferred to #45**.
- R4. Persist project state to `reports/project_state.json` so it survives a server restart. **Dropped.** #45's scope explicitly states "NOT in scope: persistence across server restart (state lives in memory only)." Rationale: the PoC is graded on architecture and clarity, not durability; in-memory state matches the existing `jobs` dict pattern in `src/qbr_web/app.py` and avoids an unnecessary file-format / eviction surface.

## Scope Boundaries

This plan owns no code. The shipped implementation belongs to #45's plan at `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md`.

## Context & Research

### Relevant Code and Patterns

- The successor plan `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md` is the authoritative reference for shipped behavior, file changes, and test scenarios.
- The `jobs` dict pattern in `src/qbr_web/app.py` (module-scope, in-memory, cleared between tests) is the precedent that justified dropping the persistence requirement.

## Key Technical Decisions

- **Close #44 in favor of #45 rather than amending #44.** Rationale: the scope change (drop persistence) was significant enough that re-specifying in a fresh issue produced a cleaner, more reviewable definition of done than editing the original. The connection is preserved via #45's body which states "Supersedes closed issue #44 (persistence explicitly out of scope)."
- **Keep this retro plan even though no code shipped under #44.** Rationale: the plans directory is intended as a one-to-one record of the issue stream so that an evaluator (or future maintainer) reading the plans in date order doesn't see a gap where #44 should be.

## Implementation Units

- [x] **Unit 1: Closed without separate implementation — see #45 plan.**

  Issue #44 was closed and superseded by #45. The dashboard feature shipped under #45's plan in PR #46 (commit `24bbe70`). No files were modified under #44.

  See: `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md`.

## Sources & References

- GitHub issue #44 (closed, superseded)
- Successor issue: #45
- Successor plan: `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md`
- Shipped in: PR #46, commit `24bbe70`
