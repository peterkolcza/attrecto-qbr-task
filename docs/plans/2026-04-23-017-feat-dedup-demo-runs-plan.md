---
title: "feat: Deduplicate demo runs — redirect to active job instead of starting a duplicate"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #36"
shipped_in: "PR #39 (commit 35b54a5)"
---

# feat: Deduplicate demo runs — redirect to active job instead of starting a duplicate

## Overview

Treat the "Process Demo Emails" button as **idempotent while a demo job is in flight**. If the user (or a second tab) clicks it again before the active demo finishes, `POST /analyze` redirects to the existing job's progress page instead of spawning a parallel pipeline over the same fixed sample data. Upload-driven analyses are still allowed to run concurrently because they carry distinct user data.

A new `source: "demo" | "upload"` field on each job record makes the demo-vs-upload distinction explicit and powers a "▶ Running" badge on the dashboard's Analysis History list.

## Problem Frame

Two clicks of "Process Demo Emails" produced two parallel pipelines over the same 18 fixed sample emails — wasted CPU (~10 min Ollama runtime each) and identical output. The existing rate limit (max 3 concurrent analyses) only kicked in at the **fourth** click, leaving three redundant runs to complete. Users seeing no feedback on the first click are exactly the population most likely to click again.

The fix is simple state hygiene: scan `jobs` for any in-flight demo before allocating a new ID, and if one exists, hand the user back the same job page they would have landed on anyway.

## Requirements Trace

- **R1** — Clicking demo while a demo job is `queued` or `processing` redirects to that job's page (no new job created). DONE — `start_analysis` scans `jobs` and returns `RedirectResponse(url=f"/jobs/{existing_id}", status_code=303)` on hit.
- **R2** — Upload flow keeps working (a second upload while one is running is **allowed** — uploads carry distinct data, so dedup would be wrong). DONE — the dedup branch is gated on `is_demo`.
- **R3** — Analysis History list visually flags currently-running jobs. DONE — `index.html` renders a pulsing `▶ Running` pill when `job.state in ('queued', 'processing')`, plus a small `job.source` label.
- **R4** — No race condition on simultaneous requests. DONE within the limits of FastAPI's single-event-loop concurrency: the scan + insert is synchronous from the start of the handler until `asyncio.create_task(...)`, so two concurrent requests can't both pass the dedup check before either creates a job (no `await` between the two operations on the dedup path). Documented as a "good enough for single-process PoC" semantic; multi-worker deployments would need a shared store.
- **R5** — Tests assert the duplicate POST returns 303 to the existing job. DONE — `tests/test_web.py::TestAnalyze::test_duplicate_demo_returns_existing_job`.
- **R6** — After a demo job completes, a new POST starts a fresh analysis. DONE — the dedup loop only matches `state in ('queued', 'processing')`, so completed/error jobs never block a new run.

## Scope Boundaries

- No upload-side dedup. Two distinct uploads can have different data; matching by some hash of the upload directory was listed in the issue but explicitly skipped to keep the change small.
- No change to the rate limit (still max 3 concurrent). Dedup is orthogonal — it removes one specific class of redundant work that the rate limit was never designed to catch.
- No persistence of the dedup decision. If the server restarts, the in-memory `jobs` dict empties and a fresh demo POST starts a new job. Acceptable for the PoC.
- No cross-process / multi-worker coordination. A single uvicorn worker is the only supported topology.

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/app.py::start_analysis` — the `POST /analyze` handler. Already had the `MAX_FILES` / `MAX_UPLOAD_SIZE` upload guards and the rate-limit check; the dedup block sits naturally between `_evict_old_jobs()` and the `job_id = str(uuid.uuid4())[:8]` allocation.
- The in-memory `jobs: dict[str, dict[str, Any]]` store and its `_evict_old_jobs()` LRU were already in place — no schema migration needed; just add a new key.
- `src/qbr_web/templates/index.html` — Analysis History list iterates `jobs.items()` and styles each entry by `job.state`. A small additive Jinja block adds the running badge without restructuring the markup.
- `tests/test_web.py` already used `TestClient(app)`. PR #39 also added an `autouse` fixture (`clear_jobs`) to wipe the in-memory `jobs` dict between tests so the new dedup test (and existing tests) don't see leftover state from earlier tests.

## Key Technical Decisions

- **Distinguish demo from upload at handler entry, before allocating the job ID.** Sets `is_demo = not (files and any(f.filename for f in files))` exactly once. Used three times: gating the dedup branch, populating `job["source"]`, and choosing the input directory.
- **Match dedup target on `source == "demo"` AND `state in ("queued", "processing")`.** Two-condition match avoids both false positives (matching an upload job) and false negatives (matching a completed/errored demo).
- **Store the source on the job dict, not in a parallel structure.** A single new key `job["source"] = "demo" | "upload"` keeps the data model trivial and the template change to one `{% if job.source %}` line.
- **303 (See Other) for the redirect**, not 302/307. 303 is the correct semantic for "after a POST, GET this resource" — guarantees the browser issues a GET on the redirect target. Same status code as the normal post-`/analyze` redirect, so the client sees no behavioural difference between "your job started" and "another job is already running for the same data."
- **No lock around the dedup scan.** FastAPI runs one event loop in this process; the scan + insert sequence has no `await` between the matching loop and the `jobs[job_id] = {...}` write, so the operation is atomic by construction. A multi-worker deployment would break this — explicitly out of scope.

## Implementation Units

- [x] **Unit 1 — Dedup branch + `source` field in `start_analysis`**

  **Goal:** Detect an in-flight demo job and short-circuit; tag every new job with its `source`.

  **Files:**
  - `src/qbr_web/app.py`

  **Approach:**
  - At the top of `start_analysis`, compute `is_demo = not (files and any(f.filename for f in files))`.
  - If `is_demo`, scan `jobs.items()` for `existing_job.get("source") == "demo" and existing_job["state"] in ("queued", "processing")` and `return RedirectResponse(url=f"/jobs/{existing_id}", status_code=303)` on first match.
  - Set `jobs[job_id]["source"] = "demo" if is_demo else "upload"` when allocating.
  - Replace the `if files and any(...)` upload-input branch with `if not is_demo:` so the same flag drives both the dedup decision and the input-source decision.

  **Test scenarios (`tests/test_web.py`):**
  - `TestAnalyze::test_duplicate_demo_returns_existing_job` — pre-seeds `jobs["abc12345"] = {..., "source": "demo", "state": "processing", ...}`, POSTs `/analyze` with no files, asserts 303 + `Location: /jobs/abc12345`.
  - `TestAnalyze::test_start_demo_analysis_redirects` — happy-path baseline (no pre-existing demo) still 303s to the **new** job's page.
  - `TestAnalyze::test_start_demo_analysis_follow` — full redirect-followed flow still works.

- [x] **Unit 2 — Dashboard "▶ Running" badge + source label in Analysis History**

  **Goal:** Make in-flight runs visible at-a-glance so users have feedback that clicking the button worked, removing the motivation to click again.

  **Files:**
  - `src/qbr_web/templates/index.html`

  **Approach:**
  - Inside the existing `{% for job_id, job in jobs %}` loop, wrap the job ID + new badges in a `flex items-center gap-2` div.
  - Add `{% if job.state in ('queued', 'processing') %}<span class="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-800 animate-pulse">▶ Running</span>{% endif %}`.
  - Add `{% if job.source %}<span class="text-xs text-gray-400">{{ job.source }}</span>{% endif %}` for the label.
  - Update the right-side state pill so `queued` shares the yellow styling already used for `processing` (was unstyled — now `bg-yellow-100 text-yellow-800`).

  **Test scenarios:**
  - Visual change verified during local web-UI smoke test; the existing `TestIndex::test_index_loads` still passes (no breaking change to the template structure).

- [x] **Unit 3 — Test isolation: `autouse` fixture to clear `jobs` between tests**

  **Goal:** Prevent leftover jobs from one test bleeding into another now that more tests inspect the dict directly.

  **Files:**
  - `tests/test_web.py`

  **Approach:**
  - Add `@pytest.fixture(autouse=True) def clear_jobs(): from qbr_web.app import jobs; jobs.clear(); yield; jobs.clear()`.
  - The `test_duplicate_demo_returns_existing_job` test still wraps its pre-seeded entry in a `try/finally` that deletes it explicitly — belt-and-braces against the fixture order changing later.

  **Test scenarios:** Implicit — every other `tests/test_web.py` test now runs against a clean `jobs` dict.

## Sources & References

- GitHub issue: [#36 — Deduplicate demo runs](https://github.com/peterkolcza/attrecto-qbr-task/issues/36)
- Pull request: [#39](https://github.com/peterkolcza/attrecto-qbr-task/pull/39) — commit `35b54a5`
- Code touched:
  - `src/qbr_web/app.py` — dedup branch + `source` field in `start_analysis`
  - `src/qbr_web/templates/index.html` — Running badge + source label
  - `tests/test_web.py` — `clear_jobs` autouse fixture + `test_duplicate_demo_returns_existing_job`
- Related: rate-limit branch (`active = sum(... in ("queued", "processing"))`) in the same handler — orthogonal but conceptually adjacent.
