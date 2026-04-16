---
title: "feat: Live project dashboard — live card updates, drill-down, real-time progress"
type: feat
status: active
date: 2026-04-16
---

# feat: Live project dashboard — live card updates, drill-down, real-time progress

## Overview

Make the dashboard project cards *actually* communicate portfolio health instead of showing static "Pending analysis" placeholders. After an analysis runs, each card reflects the latest flag state (health color, flag counts, last update). While an analysis is running, the cards update incrementally every 3s and the card for the project currently being processed pulses (a subtle, looping animation) until processing moves to the next project. Clicking a card opens a drill-down page listing that project's flags with evidence.

The entire state is held in memory — no persistence across server restart, matching the existing `jobs` dict pattern.

## Problem Frame

The task brief (`task/AI_Developer.pdf`) describes the system's core job as telling the Director "exactly where to focus their limited attention." Today the Markdown report delivers that, but the **dashboard UX does not** — after a run, the cards still say "Pending analysis" (see `src/qbr_web/templates/index.html` lines 17-19), forcing the user to open the report to discover Project Phoenix is critical. The dashboard is the landing surface; it should communicate health at a glance. This plan closes that gap.

Origin: GitHub issue #45, supersedes closed #44 (persistence explicitly out of scope).

## Requirements Trace

- R1. Project cards show live health indicator (critical/warning/good) derived from top flag severity after a completed run.
- R2. Project cards show flag counts in the form "3 flags (1 critical, 2 high)" and a `last_updated` timestamp.
- R3. While a job is running, the card for the currently-processed project shows an "Analysis in progress" badge (only on the active card, not all cards).
- R4. Clicking a card navigates to `/projects/{name}` showing all flags with evidence (quote + source), open vs resolved counts, and a link back to the most recent report.
- R5. Dashboard polls `/api/projects/state` every 3s **only while** a job is active; stops polling when no job is running.
- R6. Card values update incrementally during processing — flag counts rise as threads are classified, not only at the end.
- R7. The project whose email is currently being processed flashes briefly (visible cue; no sound).
- R8. After the job completes, the dashboard reflects the new state until the next run (no stale "Pending analysis").

## Scope Boundaries

- No persistence across server restart — `project_state` lives in memory only. First request after a restart shows seed state with `health='unknown'`.
- No websockets / SSE for the dashboard. Polling is explicitly accepted per issue spec ("polls every 3s").
- No drill-down filters/sort UI — the drill-down page shows the already-prioritized list from `aggregate_flags_by_project`.
- No edit/acknowledge actions on flags — read-only view.
- No multi-tenancy. Latest-run semantics apply globally; if two runs execute back-to-back the later one wins. Dedup (issue #36) prevents concurrent demo runs, but uploaded-email jobs can overlap (rate limit is 3). Concurrent jobs for the same project name are handled as last-writer-wins — documented as a known limitation, not a bug. No Unit 3 test scenario asserts multi-job ordering.

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/app.py` — module-scope `jobs: dict[str, dict]` pattern (line 121). Follow the same approach for `project_state`.
- `src/qbr_web/app.py` `_run_analysis` (lines 222-354) — orchestrates parse → extract per thread → classify flags → generate report. Two hook points needed: (a) per-thread start, for active-project tracking; (b) on completion, to populate `project_state` from `flags_by_project`.
- `src/qbr/flags.py` `aggregate_flags_by_project` (line 142) — already returns `dict[str, list[AttentionFlag]]` sorted by priority (critical → high → med → low, older first). Top flag's severity drives health.
- `src/qbr/models.py` `AttentionFlag` (line 121) — has `severity`, `sources[0].quoted_text`, `age_days`, `evidence_summary`. All fields the drill-down needs are already there.
- `src/qbr/seed.py` `get_demo_projects` — static baseline; live state overlays by matching on `name`.
- `src/qbr_web/templates/index.html` lines 8-42 — existing card structure (left-border color by `proj.health`). Extend same element; don't rewrite.
- `src/qbr_web/templates/job.html` lines 150-207 — reference polling pattern: `async function poll()` with `setTimeout(poll, 3000)` recursion, `fetch()` + JSON, stop on terminal state. Mirror this for the dashboard.
- `tests/test_web.py` — uses `TestClient(app)` + `autouse` fixture that clears `jobs` between tests. Apply the same pattern to `project_state`.

### Institutional Learnings

- `.planning/pdrw-35-36-37.md` — pipeline return-type change was marked as a breaking change and callers were updated together. Apply the same discipline: if `_run_analysis` signature changes, update it in one commit.
- `.planning/pdrw-41-tetris.md` — JS timeline uses `setTimeout` + feature-detect on DOM elements. Reuse the same idiom for polling start/stop so the dashboard doesn't depend on a framework.

### External References

None required — polling a JSON endpoint with `fetch()` and Tailwind utility classes are standard patterns already used in the repo.

## Key Technical Decisions

- **Store state as `dict[str, ProjectState]` keyed by project name** (not job id). Rationale: the dashboard shows current portfolio health, not per-run history. Seed project names are authoritative; live state overlays them. Jobs dict still holds per-run data (progress log, usage stats).
- **ProjectState as a plain dict**, not a Pydantic model. Rationale: follows the existing `jobs` dict convention in `app.py`. A Pydantic model adds no validation value here — all writes are internal, and the JSON endpoint can `jsonable_encoder` the dict.
- **Classify flags per thread during extraction, not only at end of run.** Rationale: R6 requires flag counts to rise incrementally. Current `_run_analysis` runs `aggregate_flags_by_project` once in Step 3 after ALL extraction completes, so there's no per-thread flag data to show. Fix: inside the per-thread loop, call `classify_flags(items, project=project)` immediately after extraction and merge results into `project_state[project]['flags']`. At end of run, still call `aggregate_flags_by_project(all_items)` — it re-sorts and attaches cross-project conflicts via `detect_conflicts`. Per-thread classify is safe: `detect_conflicts` needs the full item set but runs only at end; `classify_flags` is pure over a single-thread item list.
- **Derive health from flag presence + severity**: `critical` if any critical flag; `warning` if any flag with severity >= medium (i.e. medium, high); `good` iff `flag_count == 0` after a completed run; `unknown` for seed projects never analyzed. Rationale: a project with 8 medium-severity flags must not read as "good" — that inverts the task brief's "focus attention" mandate. Low-severity flags alone also warrant a `warning` pill because they mean attention is still required; drop the `low` bucket into `warning` with the others or filter them out upstream if that's preferable (decision: keep `low` in `warning` for now — cleaner rule).
- **Active-project tracking lives on the `job` dict**, not `project_state`. Rationale: "currently being processed" is a per-job concept. The JSON endpoint derives active project by scanning `jobs` for any `state in ('processing', 'queued')` and reading their `active_project` field.
- **Active-project clear timing**: set `job["active_project"] = thread.project or "Unknown"` at the start of each per-thread loop iteration. Clear (`None`) in two places: (a) after the extraction `try/except` block finishes that iteration, and (b) unconditionally after the whole per-thread loop ends (before Step 3 "Classifying Attention Flags…"). On exception, the clear in (a) still runs because it lives after the except, not inside it. This guarantees the flash moves cleanly between threads and doesn't persist during classification/report generation.
- **Server-side active-project minimum hold**: hold `active_project` for at least 1500ms per thread even if extraction finishes faster, so the 3s poll is statistically likely to observe each active period. Implement by comparing `time.monotonic()` before and after the extraction call; if under the minimum, `await asyncio.sleep(delta)` before clearing. Rationale: with 18 threads, some extractions complete in under 1s and the flash would never fire.
- **Polling stops client-side when the response reports no active jobs.** Rationale: server state is truth; client asks each poll whether work is ongoing. Matches the `job.html` pattern (stop when `data.state === 'complete'`).
- **Polling also starts on demand**, not only from the page-load `data-is-running` check. Specifically: after the `/analyze` POST redirect lands back on a job page, and whenever the dashboard page becomes visible (`visibilitychange` event) — call `poll()` once even if the last-known state was idle. This handles the case where a run started in another tab.
- **Shared state payload helper**: extract `_build_projects_state_payload()` in `app.py` used by both `GET /` (server render) and `GET /api/projects/state` (JSON). Produces the same shape so first paint and subsequent polls are identical. Prevents duplicating logic across the two handlers.
- **Flash animation**: custom CSS keyframe in `base.html` named `.active-flash` (not bare `animate-pulse`, to avoid class collision with the History card's "▶ Running" pill). Wrap the keyframe in `@media (prefers-reduced-motion: no-preference) { ... }`; inside `@media (prefers-reduced-motion: reduce)`, the class instead applies a static border color swap (e.g. `border-cyan-500`) so the active signal is preserved without motion. Apply `.active-flash` when `active_project` matches this card; remove when it moves on.
- **Flag serialization uses `mode='json'`**: store each flag via `AttentionFlag.model_dump(mode='json')` so embedded `datetime` fields (on `SourceAttribution.timestamp`) become ISO strings in the dict. Rationale: plain `model_dump()` leaves them as Python `datetime`, which breaks `json.dumps()` and makes the stored dict lie about being JSON-safe.
- **Drill-down link to report**: `project_state[name]['latest_job_id']` tracks the job that produced the current state. The drill-down checks `jobs.get(latest_job_id)` and renders the report link *only when that job exists and state=='complete'*. If evicted (via `_evict_old_jobs`, MAX_JOBS=20), render a disabled/muted link with a tooltip "Report no longer available — only the 20 most recent runs are retained." Rationale: silent omission leaves users wondering if the page broke; an explicit disabled state is more honest.
- **Drill-down 404 for unknown names**: if the project name is in neither `get_demo_projects()` nor `project_state`, return 404. Forgiving 200 makes the URL namespace infinite and hides typos.
- **Card affordance / HTML validity**: the existing card contains an interactive `<details>` which cannot legally be nested inside `<a>` (HTML spec forbids interactive-within-interactive). Fix: move the static body (name, PM, team size, QBR, focus, health pill, flag counts) inside an `<a href="/projects/…" class="block hover:shadow-md cursor-pointer transition">` and keep the `<details>` (and "Known risks" warning banner) *outside* the anchor as a sibling inside the card container. Alternative chosen: keep the `<details>` but collapse it into a secondary "Project details" disclosure that lives outside the link zone.

## Open Questions

### Resolved During Planning

- **How to derive health when there are no flags?** → `good` iff `flag_count == 0` after a completed run; `warning` iff any medium/high/low flag; `critical` iff any critical flag; `unknown` for seed projects never analyzed. See Key Technical Decisions for the full rule.
- **Does "Resolved" vs "Open" count belong on the drill-down?** → Yes. Compute from `flag.status` (`OPEN` vs `RESOLVED` vs `NEEDS_REVIEW`). Present as "N open · M needs review · K resolved" at the top of the drill-down (canonical order — used consistently in all test scenarios).
- **Health pill display text** → map internal state to user-facing labels in the template: `critical` → "Critical — act now", `warning` → "Attention needed", `good` → "On track", `unknown` → "Pending analysis". Rationale: the raw state strings ("warning", "good") add no signal beyond the border color; descriptive labels reinforce the "focus attention" framing.
- **Active-card badge copy** → "Analysis in progress" (matches R3). Implemented as a pulsing pill `<span class="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-800 animate-pulse">Analysis in progress</span>`, mirroring the History card's "▶ Running" pattern. No spinner (would compete with the `.active-flash` keyframe).
- **`last_updated` timestamp rendering** → render as relative time with absolute fallback. Under 24h: "Updated 3m ago" / "Updated 2h ago". Over 24h: "Updated Apr 15, 18:40". Always include the full ISO timestamp as a `title=` attribute for hover. One small JS helper reused by the History card too.
- **Drill-down empty states** → three distinct shapes:
  1. Project in seed, never analyzed: heading "No analysis yet for {name}", body "Run the demo or upload this project's emails to see its flags.", primary CTA "Run Analysis" linking to `/`.
  2. Project analyzed, zero flags: heading "All clear — no attention flags", body "The last run on {date} found nothing needing your attention.", secondary "View full report" link.
  3. Project analyzed but `latest_job_id` evicted: show flags normally, render a disabled-styled "Report no longer available" button with the explanatory tooltip.
- **`data-is-running` attribute location and values** → placed on the project grid container element (the `<div class="grid ...">` wrapping the cards). Values: `"true"` when any job is queued/processing, `"false"` otherwise. JS reads via `container.dataset.isRunning === "true"`.
- **Should the card show the timestamp even if health is `unknown`?** → No. Show `last_updated` only when a run has completed for that project.
- **What happens to cards for projects whose emails never appeared in the run?** → Stay at `unknown` health. The seed project names are the canonical set; `project_state` overlays only keys that actually received flags.
- **Where does the flash live — on every matching card or only during extraction?** → During extraction only (Stage A/B/C for that thread's project). After `_log_progress` moves past that thread, the active project flips.
- **Is URL-encoding the project name needed on the drill-down?** → Yes. Names contain spaces and non-ASCII (e.g., "DivatKirály"). Use FastAPI's automatic path-param decoding; on the frontend, `encodeURIComponent(proj.name)` when constructing the link.

### Deferred to Implementation

- **Exact CSS keyframe tuning for the flash** (duration 1.0–1.5s, opacity/border-color curve) — tune during the UI check against the real running job. The structural decision (custom keyframe, reduced-motion fallback, scoped class name) is resolved above.
- **Whether to also tween numeric count changes** (e.g. a 600ms background-highlight on the count when it increments) — add if the raw DOM updates feel stuttery in practice. Falls back to plain updates under `prefers-reduced-motion: reduce`.

## Implementation Units

- [ ] **Unit 1: ProjectState store + population on job completion**

  **Goal:** Add `project_state: dict[str, dict]` module-scope in `src/qbr_web/app.py`. Populate it at the end of `_run_analysis` from `flags_by_project`. Each entry records health, top severity, flag counts, serialized flags, timestamp, and the job id that produced it.

  **Requirements:** R1, R2, R8.

  **Dependencies:** None.

  **Files:**
  - Modify: `src/qbr_web/app.py`
  - Test: `tests/test_web.py`

  **Approach:**
  - Add module-scope `project_state: dict[str, dict[str, Any]] = {}` next to the `jobs` dict.
  - Add a helper (e.g. `_finalize_project_state(flags_by_project, job_id)`) that writes one entry per project in `flags_by_project`. Computes health per the rule in Key Technical Decisions (critical → critical, any other flags → warning, zero flags → good). Counts flags by severity. Serializes each flag via `AttentionFlag.model_dump(mode='json')` so embedded `datetime` fields become ISO strings.
  - Call the helper in `_run_analysis` right after `aggregate_flags_by_project` returns and before report generation, so the dashboard reflects flag counts even if report generation fails.
  - Ensure projects not present in `flags_by_project` (because no items were extracted) are not overwritten — dashboard continues to show `unknown` for them.
  - The finalize helper **merges, not replaces**: it uses the end-of-run `flags_by_project` as the source of truth for counts/health (since `detect_conflicts` may have attached cross-project conflicts only now), but it must not regress `flag_count` below what Unit 2 already wrote incrementally. If the incremental count is higher (should not happen, but defensively), keep the higher value and log a warning.

  **Patterns to follow:**
  - `jobs` dict lifecycle in `src/qbr_web/app.py` (module scope, cleared in tests via `autouse` fixture).
  - `AttentionFlag` already has `model_dump` via Pydantic.

  **Test scenarios:**
  - Happy path: given a `flags_by_project` with one project having 1 critical + 2 high flags, `project_state[name]['health'] == 'critical'`, `['flag_count'] == 3`, `['critical_count'] == 1`, `['high_count'] == 2`, `['last_updated']` is set, `['flags']` is a list of dicts.
  - Happy path: project with only `medium` flags → health `'good'` (threshold defined in Key Technical Decisions).
  - Edge case: empty flag list for a project that DID get processed → health `'good'`, counts all zero.
  - Edge case: project in seed but absent from `flags_by_project` → `project_state` unchanged for that key (not overwritten with empty state).
  - Integration: after running a fake pipeline through `_run_analysis` with mocked LLM clients, `project_state` is populated for every project that produced flags.

  **Verification:**
  - Unit tests pass for the helper in isolation.
  - After a successful `/analyze` demo run in a live server, `GET /api/projects/state` returns non-empty entries (covered by Unit 3 tests).

- [ ] **Unit 2: Active-project tracking + per-thread flag classification**

  **Goal:** As each thread is processed, record the project on the job record and classify that thread's flags immediately, merging into `project_state`. This satisfies both R7 (active-card flash) and R6 (counts rise incrementally during processing, not only at the end).

  **Requirements:** R3, R6, R7.

  **Dependencies:** Unit 1 (both write to `project_state`; Unit 2 writes incremental per-thread flags, Unit 1 finalizes at end of run).

  **Files:**
  - Modify: `src/qbr_web/app.py`
  - Modify: `src/qbr/flags.py` (only if a helper to merge per-thread flags into a project bucket is cleaner there; otherwise keep the merge logic in `app.py`)
  - Test: `tests/test_web.py`

  **Approach:**
  - Initialize `job["active_project"] = None` in the `/analyze` handler where the job dict is built.
  - In `_run_analysis`, at the **start of each per-thread iteration** (right after `_log_progress` announces the thread), set `job["active_project"] = thread.project or "Unknown"` and record `t_start = time.monotonic()`.
  - Wrap the `run_pipeline_for_thread` call in a `try/except` (already there). After the try/except completes — whether success or handled exception — run the **clear + hold** sequence: compute `elapsed = time.monotonic() - t_start`; if `elapsed < 1.5`, `await asyncio.sleep(1.5 - elapsed)`. Then set `job["active_project"] = None`. This guarantees each active period is observed at least once by a 3s poll.
  - **Per-thread classification:** inside the same try block, right after `all_items[project].extend(items)`, call `per_thread_flags = classify_flags(items, project=project)` and merge into `project_state[project]`. Update `flag_count`, `critical_count`, etc. Append serialized flags (via `AttentionFlag.model_dump(mode='json')`) to `project_state[project]['flags']`. Do NOT call `detect_conflicts` per thread — it needs the full item list and runs at end of job via `aggregate_flags_by_project`.
  - After the per-thread loop ends (before Step 3 "Classifying Attention Flags…"), defensively set `job["active_project"] = None`.
  - On pipeline-wide exception (the outer try in `_run_analysis`), clear `active_project` in the `except` branch so error states don't leave a stale value.

  **Execution note:** Implement the active-project timing test-first — the 1.5s hold + poll-observability claim is the kind of timing bug that is easy to break in refactors.

  **Patterns to follow:**
  - `_log_progress` call placement in `_run_analysis` (lines 264-317) — hook into the same per-thread loop.

  **Test scenarios:**
  - Happy path: during a mocked pipeline run that processes two threads for "Project Phoenix" then one for "DivatKirály", at each point the job's `active_project` matches the current thread's project.
  - Edge case: thread with `project = ""` → `active_project == "Unknown"`.
  - Edge case: pipeline raises mid-thread → `active_project` is cleared (no stale value visible after job ends in error state).
  - Integration: `/api/projects/state` (Unit 3) reports the correct `active_project` while the pipeline is mid-run.

  **Verification:**
  - A unit test using an async task that suspends between threads can assert `active_project` transitions correctly.

- [ ] **Unit 3: `GET /api/projects/state` JSON endpoint**

  **Goal:** Expose a single JSON endpoint the dashboard polls. Returns every seed project merged with live state, plus a top-level `active_project` and `is_running` flag.

  **Requirements:** R1, R2, R3, R5, R6.

  **Dependencies:** Unit 1 (writes `project_state`), Unit 2 (writes `job.active_project`).

  **Files:**
  - Modify: `src/qbr_web/app.py`
  - Test: `tests/test_web.py`

  **Approach:**
  - Route: `GET /api/projects/state`.
  - Response shape:

        {
          "is_running": true,
          "active_project": "Project Phoenix",
          "projects": {
            "Project Phoenix": {"health": "critical", "flag_count": 3, "critical_count": 1, "high_count": 2, "last_updated": "2026-04-16T18:40:00Z"},
            "Project Omicron": {"health": "unknown"},
            "DivatKirály":     {"health": "good", "flag_count": 0, "last_updated": "..."}
          }
        }

  - `is_running` = `any(j["state"] in ("queued", "processing") for j in jobs.values())`.
  - `active_project` = first such job's `active_project` (or `null`).
  - `projects` = merge seed names (from `get_demo_projects()`) with live state; for unseeded projects (uploaded-email case with a new project name), include them too.
  - Do NOT return the full `flags` list here — that belongs to the drill-down endpoint (not needed every 3s).

  **Patterns to follow:**
  - `job_progress` handler (lines 367-379) for endpoint shape.

  **Test scenarios:**
  - Happy path: no jobs running, no project_state written → every seed project returns `health='unknown'`, `is_running=False`, `active_project=None`.
  - Happy path: after Unit 1 populates state → returned project has `health`, `flag_count`, etc.
  - Happy path: with a processing job that has `active_project='Project Phoenix'` → response `is_running=True`, `active_project='Project Phoenix'`.
  - Edge case: uploaded-email job produces flags for a project name not in the seed → that project appears in the response too.
  - Edge case: concurrent jobs behavior is explicitly not asserted — last-writer-wins is the documented semantic (see Scope Boundaries). The endpoint still returns the first match defensively.

  **Verification:**
  - `curl /api/projects/state` on a running server returns the documented shape.

- [ ] **Unit 4: Dashboard index.html live updates + flash animation**

  **Goal:** Update `index.html` project cards to render live health, flag counts, timestamp, and "Analysis in progress" badges. Poll `/api/projects/state` every 3s while `is_running`; flash the card matching `active_project`.

  **Requirements:** R1, R2, R3, R5, R6, R7, R8.

  **Dependencies:** Unit 3.

  **Files:**
  - Modify: `src/qbr_web/app.py` (`index()` handler + new `_build_projects_state_payload()` helper)
  - Modify: `src/qbr_web/templates/index.html`
  - Modify: `src/qbr_web/templates/base.html` (CSS keyframe for flash + `prefers-reduced-motion` fallback)
  - Test: `tests/test_web.py`

  **Approach:**
  - **Shared payload helper**: extract `_build_projects_state_payload()` in `app.py`. Returns the same dict shape used by `/api/projects/state` (see Unit 3). Both `index()` and `projects_state()` call it. First paint and polls are identical.
  - **Server render**: `index()` passes the payload into the template so the first paint already reflects live state (no flash on load).
  - Replace the static "Pending analysis" chunk (lines 16-19) with a conditional that shows:
    - `unknown` → pill "Pending analysis" (current behavior).
    - `good` → green pill "On track" + "0 flags".
    - `warning` → yellow pill "Attention needed" + count line.
    - `critical` → red pill "Critical — act now" + count line.
    - Count line format: compact inline form `<severity-dot>1 · <severity-dot>2 · <severity-dot>0` (critical · high · medium, with tiny colored dots), and a separate small-text "N flags total" below. Avoids the parenthetical-duplication-of-the-word-"flags" problem.
    - `last_updated` rendered by a small JS helper: "Updated 3m ago" / "Updated 2h ago" / "Updated Apr 15, 18:40". ISO string in `title=` attribute.
    - If `active_project === proj.name` → show the "Analysis in progress" pulsing pill (mirrors the History card "▶ Running" pattern).
  - **Card affordance / anchor structure**: wrap the name+pill+counts+timestamp block inside `<a href="/projects/{{ proj.name | urlencode }}" class="block hover:shadow-md cursor-pointer transition">`. Keep the `<details class="team">` and known-risks banner **outside** the anchor (siblings inside the card container). Rationale: nested interactive elements inside `<a>` violate HTML spec and break the `<details>` toggle.
  - **JS polling block** at the bottom of `index.html`:
    - On page load, read the grid container's `data-is-running` attribute. Also register a `visibilitychange` listener and a manual `poll()` hook after navigations — so polling starts when a run began in another tab.
    - If `is_running`, `setTimeout(poll, 3000)` loop; otherwise don't poll.
    - Each poll updates card DOM by `data-project-name` attribute lookup. Update pill class/text, counts, timestamp, active-badge presence.
    - Stop polling when a response returns `is_running=false` (apply the final render once; keep the "Updated Xm ago" helper running on an interval independent of polling).
    - Maintain an `aria-live="polite"` visually-hidden `<div role="status" class="sr-only">` and announce health changes: "Project Phoenix is now critical with 3 flags."
  - **Flash animation** (`base.html`):
    - Add `.active-flash` CSS class with a 1.0–1.5s keyframe pulsing `border-color` + subtle `box-shadow`. Scoped to the class; does NOT apply `animate-pulse` on the card root.
    - Wrap the keyframe rule in `@media (prefers-reduced-motion: no-preference) { ... }`. Inside `@media (prefers-reduced-motion: reduce) { .active-flash { border-color: <cyan-500>; box-shadow: none; } }` — static state preserves the "this is the active one" signal without motion.
    - Add the `.just-updated` helper class (600ms background-highlight on incremented numbers), also gated by the no-preference media query.

  **Patterns to follow:**
  - `job.html` lines 150-207 — polling loop with terminal-state exit.
  - Existing card DOM in `index.html` — minimal structural change; only replace the health badge block and wrap in `<a>`.

  **Test scenarios:**
  - Happy path (server render): after Unit 1 populates state, `GET /` returns HTML containing "1 critical" for Project Phoenix's card (when that's the test fixture).
  - Happy path (server render): empty `project_state` → page still renders with "Pending analysis" for all three seed projects. Existing `TestIndex.test_index_loads` must still pass.
  - Happy path: card anchors link to `/projects/{name}` with URL-encoded name.
  - Edge case: `active_project=None` → no card carries `active-flash` class.
  - Edge case: project in `project_state` but not in seed (uploaded-email edge case) → still rendered (existing behavior overlays seed only, so this is acceptable; document as known limitation for demo data).
  - Integration: JS polling is client-side; covered by a manual browser check during ce:work, not a Python test. The unit test asserts server-side HTML includes the expected data attributes (`data-project-name`, `data-is-running`).

  **Verification:**
  - Manual: `make web`, trigger a demo run, watch cards update and the active one flash.
  - Automated: HTML snapshot assertions in `test_web.py` for the server-rendered shape.

- [ ] **Unit 5: `GET /projects/{name}` drill-down page**

  **Goal:** New page showing all flags for a given project with evidence, open/resolved counts, and a link back to the latest job's report.

  **Requirements:** R4.

  **Dependencies:** Unit 1 (writes `flags` into `project_state`).

  **Files:**
  - Create: `src/qbr_web/templates/project_detail.html`
  - Modify: `src/qbr_web/app.py`
  - Test: `tests/test_web.py`

  **Approach:**
  - Route: `GET /projects/{name}` — path param is the project name (FastAPI handles URL-decoding).
  - Resolve the name via `resolve = get_demo_projects()` (check by name) + `project_state`. If the name is in neither, return **404**. Rationale: forgiving 200 makes the URL namespace infinite and hides typos.
  - If the project is in seed but `project_state[name]` is missing → render empty-state #1 ("No analysis yet for {name}", CTA "Run Analysis").
  - If `project_state[name]` exists with `flag_count == 0` → render empty-state #2 ("All clear — no attention flags", secondary "View full report" link).
  - Otherwise render the full drill-down:
    - Header with project name, health pill (using the descriptive label map), flag-count summary.
    - Status counts: "N open · M needs review · K resolved" (canonical order) derived from `flag['status']` across `project_state[name]['flags']`.
    - Flag list: title, severity badge, evidence block (`evidence_summary` already contains `"quote" — person (source_ref)`), age in days.
    - "View full report" link:
      - If `jobs.get(latest_job_id)` exists and `state == 'complete'`: render as primary button linking to `/jobs/{id}/report`.
      - Else: render as **disabled styled** button (text-gray-400, cursor-not-allowed, no href) with `title="Report no longer available — only the 20 most recent runs are retained."` Rationale: explicit disabled state beats silent omission.
    - "Back to dashboard" link.
  - Reuse `base.html` layout and Tailwind classes from `report.html`/`index.html` for visual consistency.
  - Escape the project name in the template (Jinja's default autoescape) regardless of source — defense in depth even though 404 gates the set.

  **Patterns to follow:**
  - `job_report` handler (lines 436-449) for template-response shape.
  - `report.html` for flag presentation styling (severity color map).

  **Test scenarios:**
  - Happy path: `project_state` has "Project Phoenix" with 3 flags → `GET /projects/Project%20Phoenix` returns 200 and HTML contains each flag's title, severity, and evidence quote.
  - Happy path: status counts render correctly ("1 open · 2 needs review · 0 resolved" — canonical order/spelling) for a sample fixture.
  - Edge case: project name in seed but never analyzed → returns 200 with empty-state #1 copy and a "Run Analysis" CTA.
  - Edge case: project analyzed with zero flags → 200 with empty-state #2 ("All clear") + secondary report link.
  - Edge case: unknown project name (not in seed, not in state) → **404** (forgiving 200 removed after review).
  - Edge case: `latest_job_id` points to an evicted job → page renders flags + a **disabled-styled** report link with tooltip, not silent omission.
  - Edge case: project name with non-ASCII ("DivatKirály") — URL round-trips correctly (ASGI/FastAPI handles UTF-8 path params).

  **Verification:**
  - Click a card on the running dashboard → land on the drill-down → see flags with evidence → click back.

## System-Wide Impact

- **Interaction graph:** `_run_analysis` gains two write points (`project_state` at completion, `job.active_project` per thread). No other callers. Auth middleware protects the new `/api/projects/state` and `/projects/{name}` routes automatically (neither is in `is_public_path`'s allowlist — existing behavior is what we want).
- **Error propagation:** If `_update_project_state_from_flags` raises, wrap the call in a try/except that logs and continues so dashboard failure never breaks the main pipeline. Report generation remains the source of truth; dashboard is an observability layer.
- **State lifecycle risks:** `project_state` grows unbounded if many unique project names are uploaded (each upload's project names accumulate). Acceptable for PoC (max project set is <10 in practice); document as a known limit. `jobs` has `_evict_old_jobs` (MAX_JOBS=20) which may evict the `latest_job_id` referenced by a state entry — the drill-down must handle that gracefully (already covered in Unit 5 edge case).
- **API surface parity:** `/api/jobs/{id}/progress` already returns JSON; `/api/projects/state` is a sibling. Same content type, same no-auth-on-API caveat (currently `is_public_path` does NOT exempt `/api/*` — auth is enforced. That's correct; dashboard polling happens post-login).
- **Integration coverage:** The polling loop + server state + flash timing are a three-layer interaction; unit tests for each piece plus one manual end-to-end run during ce:work.
- **Unchanged invariants:**
  - `jobs` dict structure (progress list, result, state enum) — untouched except for the new `active_project` key which defaults to `None`.
  - `_evict_old_jobs` behavior — untouched.
  - `aggregate_flags_by_project` return shape — untouched; this plan consumes it.
  - `get_demo_projects` seed — untouched; `project_state` overlays by key match.
  - Auth middleware allowlist — untouched. New routes are authenticated like everything else.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Dashboard polling adds server load with many concurrent viewers. | Polling is 3s per client, endpoint is O(projects) on in-memory dicts — trivial cost. Document the assumption. If ever a concern, swap to SSE later. |
| `project_state` and `jobs` can diverge if a job errors partway. | On `state='error'`, leave `project_state` as-is (pre-error counts still reflect what was processed). The error badge on the History card already signals "that run failed." |
| Flash animation becomes distracting or accessibility-hostile. | `prefers-reduced-motion: reduce` media query disables the keyframe and substitutes a static border color, preserving the active-card signal without motion (resolved in Key Technical Decisions and Unit 4). |
| Unicode project names break URL routing on some hosts. | FastAPI + Starlette handle UTF-8 path params correctly. Covered by Unit 5 edge-case test. |
| Client-side DOM updates race with server render on load. | First paint uses server-rendered state (Unit 4), then client polling only mutates DOM when values change. No race; polling is additive. |
| `latest_job_id` eviction leaves a dangling report link. | Drill-down renders the link as disabled-styled with tooltip, not silent omission (Unit 5). |
| Concurrent uploaded-email jobs touching the same project name race on `project_state`. | Last-writer-wins is accepted for the PoC — the UI always reflects the most recent state. Document as a known limitation. In production, add an `asyncio.Lock` around `project_state` writes plus a monotonic `generation` counter so readers can detect stale state. |
| Short-lived threads never produce a visible flash. | Server holds `active_project` for at least 1.5s per thread (resolved in Unit 2 + Key Technical Decisions). |
| Screen-reader users miss polled changes. | `aria-live="polite"` region announces health transitions on each poll (Unit 4). |
| `<details>` inside `<a>` breaks the team-disclosure toggle. | Card structure restructured: the anchor wraps only the clickable body; `<details>` lives as a sibling outside the anchor (Unit 4). |

## Documentation / Operational Notes

- No README changes required — the feature is UI-only and the flow is self-explanatory.
- No env var changes.
- `Blueprint.md` already cites "focus attention" as the system's core value — the PR description should reference that framing and issue #45.

## Sources & References

- Related issue: #45
- Supersedes: #44 (closed — persistence out of scope)
- Related code:
  - `src/qbr_web/app.py` (jobs dict, `_run_analysis`, route handlers)
  - `src/qbr_web/templates/index.html` (existing card structure)
  - `src/qbr_web/templates/job.html` (polling reference pattern)
  - `src/qbr/flags.py` (`aggregate_flags_by_project`)
  - `src/qbr/seed.py` (`get_demo_projects`)
  - `src/qbr/models.py` (`AttentionFlag`)
- Prior plans: `.planning/pdrw-35-36-37.md`, `.planning/pdrw-41-tetris.md`
