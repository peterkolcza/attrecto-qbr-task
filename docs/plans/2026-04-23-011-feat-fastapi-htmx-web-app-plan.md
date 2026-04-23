---
title: "feat: FastAPI + HTMX + Tailwind web app with SSE progress, dashboard, and report view"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #11"
shipped_in: "PR #24 (commit 826795a)"
---

# feat: FastAPI + HTMX + Tailwind web app with SSE progress, dashboard, and report view

## Overview

Wrap the existing `qbr` core pipeline with a deployable web UI so a non-CLI evaluator can trigger a demo run, watch the pipeline tick through each email in real time, and read the final Portfolio Health Report in the browser. The app is intentionally lightweight: FastAPI + Jinja2 templates + Tailwind CDN + HTMX (with the SSE extension) — no Node build step, no database, no Celery. Job state lives in a module-scope `dict` and analyses run as `asyncio.create_task(...)` in the same process.

This is the first real surface for the web product. Issue #14 layers seed projects + portfolio cards on top of the same templates; issues #44/#45 later replace the SSE flow with a polling dashboard. Both build on the skeleton landed here.

## Problem Frame

The CLI pipeline exists (`make run`) but the evaluator brief calls out "working PoC code" alongside the Blueprint, and a graded review is much faster on a hosted URL than on a clone-and-`uv-sync` flow. A web UI also makes the "real-time processing visibility" angle (per-email progress, prompts/responses, token usage) tangible in a way the CLI logs cannot. Issue #11 carves out the technical surface — routes, templates, background processing, SSE — and explicitly defers richer dashboard semantics to #14.

## Requirements Trace

- R1. **DONE** — `src/qbr_web/` module exists, importing only from `qbr.*`; no business logic duplicated (`src/qbr_web/app.py:24-29`).
- R2. **DONE** — `GET /` serves a landing/dashboard with a "Run Demo" button + file upload form (`src/qbr_web/templates/index.html`).
- R3. **DONE** — `POST /analyze` creates a job, stores it in the in-memory `jobs` dict, and kicks off `_run_analysis` via `asyncio.create_task(...)`.
- R4. **DONE** — `GET /api/jobs/{id}/stream` returns an `EventSourceResponse` from `sse-starlette` with `progress` / `complete` / `error` events.
- R5. **DONE** — `GET /jobs/{id}/report` renders the synthesized Markdown report as sanitized HTML with a per-project flags sidebar (collapsible `<details>` per flag).
- R6. **DONE** — Real-time processing log: `job.html` renders prior log lines server-side and the SSE/HTMX wiring streams new ones; per-email progress shows file + subject + extraction model.
- R7. **DONE** — File upload accepts `.txt`, max 5 MB per file (`MAX_UPLOAD_SIZE = 5 * 1024 * 1024`), uses `python-multipart` via FastAPI's `UploadFile`.
- R8. **DONE** — Tailwind via CDN, HTMX 2.0.4 + `htmx-ext-sse` 2.2.2 via unpkg — zero build step (`src/qbr_web/templates/base.html`).
- R9. **DONE** — `/healthz` endpoint returns `{"status": "ok", "timestamp": ...}` for the Caddy/Docker healthcheck.

## Scope Boundaries

- **No persistence.** `jobs` lives in memory; restarting the process loses history. SQLite persistence was in the issue body but explicitly cut for the PoC — documented as a deferred concern.
- **No auth.** Login + session middleware land later in a follow-up; the PR ships an open app.
- **No Celery/Redis.** Background work is `asyncio.create_task(...)` in the same process. Acceptable because each analysis is single-tenant and short-lived (≤ a few minutes).
- **No portfolio dashboard.** Health-indicator cards and the pre-seeded project view are the territory of issue #14, intentionally deferred.
- **SSE not unit-tested.** Sync `TestClient` cannot consume an `EventSourceResponse`; the SSE flow is verified manually and `TestSSEStream` is left as a placeholder class with an explanatory docstring.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/pipeline.py` — `run_pipeline_for_thread(thread, client, colleagues, model)` is the per-thread entry point; the web layer drives it inside its own loop so it can log between threads.
- `src/qbr/parser.py` — `parse_all_emails(input_dir)` returns the thread list the web layer iterates over.
- `src/qbr/flags.py` — `aggregate_flags_by_project(items_by_project)` is the cross-thread classifier called once at the end of the run.
- `src/qbr/report.py` — `generate_report` produces the Markdown; `build_report_json` produces the structured payload the sidebar consumes.
- `src/qbr/llm.py` — `create_client` (later `create_hybrid_clients`) + `UsageTracker` give the web layer LLM-call accounting it can render in the result panel.
- The CLI in `src/qbr/cli.py` was the structural reference for orchestration ordering (parse → extract per thread → classify → synthesize) so the web layer mirrors the same step sequence and labels.

## Key Technical Decisions

- **Module-scope `jobs: dict[str, dict]` instead of a database.** Rationale: PoC-grade and the simplest thing that lets multiple HTTP requests see the same job state. Idempotent restart loss is acceptable; the job page only needs to live as long as a single demo run plus a few minutes of report viewing.
- **`asyncio.create_task(_run_analysis(...))` for background work.** Rationale: avoids a worker process; the pipeline's heavy work is wrapped in `await asyncio.to_thread(...)` so blocking calls (network LLM, file parsing) don't stall the event loop. Trades horizontal scaling for zero infrastructure.
- **SSE over polling for the live log.** Rationale (from the issue body): SSE is unidirectional, works through proxies (matters for the Caddy deploy in #12), and avoids "is it done yet?" polling. The `event_generator` reads `job["progress"]` from index `last_idx` so reconnects can resume correctly.
- **Tailwind + HTMX via CDN, no build step.** Rationale: the evaluator should be able to clone, `uv sync`, and `uvicorn qbr_web.app:app` without touching npm. Acceptable trade: production CSP must allow `https://cdn.tailwindcss.com` and `https://unpkg.com` (set in the Caddyfile).
- **Markdown sanitisation with `bleach`.** Rationale: report HTML is rendered from LLM output. The `_md_to_html` filter restricts the tag/attribute allowlist to what the report actually needs, defending against prompt-injected `<script>` or `<iframe>` payloads. Treating the LLM as untrusted output is the same posture the parser takes for input emails.
- **Job IDs are 8-char UUID prefixes.** Rationale: short enough to read in URLs, low collision risk for the in-memory `MAX_JOBS=20` window introduced later. The pattern fits a demo where humans need to quote IDs.
- **First-render-then-stream UX on the job page.** The template loops over `job.progress` server-side so reloads don't lose context, and the SSE/HTMX block only appends *new* lines (`sse-swap="progress"`). Rationale: refresh-resilient.

## Implementation Units

- [x] **Unit 1: FastAPI app skeleton + healthcheck**
  - **Goal:** Stand up `src/qbr_web/app.py` with the FastAPI app, Jinja2 template loader, static-file mount, and a `/healthz` route the deployment layer can poll.
  - **Files:** `src/qbr_web/app.py`, `src/qbr_web/__init__.py`, `src/qbr_web/static/.gitkeep`.
  - **Approach:** Mount `templates/` via `Jinja2Templates(directory=BASE_DIR / "templates")`. Disable template caching (`templates.env.cache_size = 0`) — Jinja2's hashing of the request object as a cache key surfaced a `TypeError: unhashable type` in dev, and the cache buys nothing for a tiny app. `/healthz` returns `{"status": "ok", "timestamp": datetime.now().isoformat()}`.
  - **Test scenarios:** `tests/test_web.py::TestHealthcheck::test_healthz` asserts 200 + `{"status": "ok"}` shape.

- [x] **Unit 2: Landing page + Jinja base template**
  - **Goal:** `GET /` renders a two-column layout: "Run Analysis" card (demo button + upload form) and "Recent Jobs" card.
  - **Files:** `src/qbr_web/templates/base.html`, `src/qbr_web/templates/index.html`.
  - **Approach:** `base.html` is the single shared layout — DOCTYPE, Tailwind CDN, HTMX + HTMX-SSE script tags, a `.prose` style block for report rendering, and a top nav with the project title. `index.html` extends `base.html` and renders the two cards plus a "How It Works" 4-step explainer. The demo button is conditionally rendered when `SAMPLE_DATA_DIR` exists so the same template works without bundled emails.
  - **Test scenarios:** `TestIndex::test_index_loads` asserts page contains `"QBR"` and `"Run Demo"` (issue #14 later changes this to `"Process Demo Emails"` and the test follows). `TestIndex::test_index_has_upload_form` asserts the upload affordance is present.

- [x] **Unit 3: `POST /analyze` + in-memory job store**
  - **Goal:** Accept either an empty form (use bundled `task/sample_data/`) or a file upload (write to `/tmp/qbr_uploads/{job_id}/`), create a job dict, and start the background task.
  - **Files:** `src/qbr_web/app.py`.
  - **Approach:** Generate `job_id = str(uuid.uuid4())[:8]`, seed `jobs[job_id]` with `state="queued"`, an empty `progress` list, `result=None`, and an ISO `created_at`. For uploads, iterate `files: list[UploadFile]`, filter to `.txt`, save under a per-job temp dir. Then `asyncio.create_task(_run_analysis(job_id, input_dir))`. Return `{"job_id": ..., "status": "queued"}` JSON. (Later PRs change this to a 303 redirect to `/jobs/{id}`.)
  - **Test scenarios:** `TestAnalyze::test_start_demo_analysis` asserts the JSON shape; `TestAnalyze::test_job_detail_page` asserts the redirect lands on a page that contains the job id.

- [x] **Unit 4: `_run_analysis` background task**
  - **Goal:** Drive the pipeline (parse → extract per thread → classify → synthesize), logging each step into `job["progress"]` so the SSE stream and refresh-rendered log can replay the run.
  - **Files:** `src/qbr_web/app.py`.
  - **Approach:** Build the LLM client + `UsageTracker` from env vars (`QBR_LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, `OLLAMA_MODEL`). Wrap each blocking step in `await asyncio.to_thread(...)`. Per-thread loop logs progress with `[{i+1}/{n}] Processing: {subject[:60]}` then `→ {len(items)} items ({open_count} open)`. On exception in the per-thread try, log `⚠ Error: {e}` and continue; on outer exception, set `state="error"` and store the error string. Finalize with `Complete! {tracker.total_calls} LLM calls, ${tracker.total_cost_usd:.4f}`.
  - **Test scenarios:** Covered indirectly via `test_start_demo_analysis` (background task starts) and the manual SSE check; later PRs add unit coverage for the helpers it spawns (project state, dedup).

- [x] **Unit 5: SSE stream + job detail page**
  - **Goal:** `GET /api/jobs/{id}/stream` emits incremental progress events; `GET /jobs/{id}` renders the page with the existing log + a live-update region.
  - **Files:** `src/qbr_web/app.py`, `src/qbr_web/templates/job.html`.
  - **Approach:** `job_stream` returns `EventSourceResponse(event_generator())`. `event_generator` tracks `last_idx` over `job["progress"]` and yields `{"event": "progress", "data": json.dumps(msg)}` for each unsent line, then sleeps 0.5s between ticks. Terminal events: `complete` (carries a 500-char Markdown preview + `usage` summary) or `error` (carries `{"error": ...}`). `job.html` renders prior progress server-side inside `#log-container` and uses the HTMX SSE extension (`hx-ext="sse"` + `sse-connect` + `sse-swap`) to append new entries; the "Result" column flips to a "View Full Report" button on `complete`.
  - **Verification:** SSE flow tested manually (sync `TestClient` can't consume an `EventSourceResponse`); `TestSSEStream` is left as a placeholder docstring noting this. `TestAnalyze::test_job_detail_page` asserts the page renders for a known job id, `TestAnalyze::test_job_not_found` asserts a 404 for unknown ids.

- [x] **Unit 6: Report view with sanitised Markdown + flags sidebar**
  - **Goal:** `GET /jobs/{id}/report` renders the final report as HTML alongside a per-project flags panel with collapsible evidence.
  - **Files:** `src/qbr_web/app.py`, `src/qbr_web/templates/report.html`.
  - **Approach:** Register a custom Jinja filter `_md_to_html` that pipes through `markdown.markdown(..., extensions=["tables", "fenced_code"])` then `bleach.clean(...)` with a fixed allowlist of safe tags (`h1-h4`, `p`, `ul/ol/li`, `strong`, `em`, `code`, `pre`, `blockquote`, `table` family, `hr`, `br`, `a` with `href`). Template uses `{{ report_md | markdown | safe }}` inside a `.prose` wrapper. Sidebar iterates `report_json.flags_by_project`, rendering each flag inside `<details>` with a severity dot (`bg-red-500` / `bg-orange-500` / `bg-yellow-500` / `bg-green-500`), type, age, source person + ref, and a quoted evidence blockquote. Stats card up top shows projects analyzed, total flags, critical count, and generated-at timestamp.
  - **Verification:** Page renders end-to-end on a completed demo job; sidebar `<details>` toggle the evidence per flag.

## System-Wide Impact

- **New top-level package** `src/qbr_web/` runs alongside `src/qbr/`. Production entry point becomes `uvicorn qbr_web.app:app` — used by `make web`, the Dockerfile `CMD`, and the Oracle smoke test.
- **`pyproject.toml` `[project.optional-dependencies].web`** gains `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `sse-starlette`, `markdown`, `bleach`. The Docker build installs with `--extra web`.
- **`/tmp/qbr_uploads/{job_id}/` is the upload sink.** Cleanup ships in a follow-up; the demo path bypasses this entirely by reading directly from `task/sample_data/`.
- **Healthcheck contract.** `/healthz` is consumed by the Dockerfile `HEALTHCHECK`, the docker-compose `healthcheck`, and the Oracle smoke test (`deploy/smoke-test.sh`). Changing its response shape is now a cross-cutting concern.
- **Defines the canvas for #14 (seed/dashboard) and #44/#45 (live polling).** The `jobs` dict, the `_run_analysis` step ordering, and the template extension points are all reused by those follow-ups; major restructures here would have downstream cost.

## Sources & References

- GitHub issue: #11 — "FastAPI + HTMX + Tailwind web app"
- Shipping commit: `826795a` (PR #24)
- Related code:
  - `src/qbr_web/app.py` — FastAPI app, routes, in-memory `jobs` dict, `_run_analysis` background task, SSE generator
  - `src/qbr_web/templates/base.html` — shared layout, Tailwind/HTMX CDN script tags, `.prose` styling
  - `src/qbr_web/templates/index.html` — landing/dashboard cards
  - `src/qbr_web/templates/job.html` — live log + result panel
  - `src/qbr_web/templates/report.html` — Markdown report + flags sidebar
  - `tests/test_web.py` — `TestHealthcheck`, `TestIndex`, `TestAnalyze`, `TestSSEStream` (placeholder)
- Downstream: #12 (Docker/Caddy), #13 (Oracle runbook), #14 (seed + dashboard), #44/#45 (live polling dashboard)
