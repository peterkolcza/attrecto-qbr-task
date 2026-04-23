---
title: "feat: Demo seed data + portfolio dashboard cards (first cut)"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #14"
shipped_in: "PR #27 (commit e4d4f97)"
---

# feat: Demo seed data + portfolio dashboard cards (first cut)

## Overview

Make the dashboard a populated portfolio view *before* the evaluator runs anything: three pre-loaded projects (Project Phoenix, Project Omicron, DivatKirály) with PMs, team rosters from `Colleagues.txt`, Q3 focus, known risks, and QBR dates. The landing page renders a card per project with a left-border health indicator (defaulting to `unknown` until an analysis runs), team disclosure, and a "Process Demo Emails" CTA framed in dashboard terms. The CLI gains a `qbr seed-demo` command that prints the same data as Rich tables for offline inspection.

This is the **first cut** of the dashboard. Issue #14's full ambition (real-time per-thread updates, provenance with conflict detection, drill-downs) was scoped down to "render seed-state cards + a CTA"; the live-update layer, drill-down page, and provenance richening land later in #44/#45 (live polling) and the broader provenance work elsewhere in the repo. Calling it the first cut here is honest about what shipped vs. what the issue body described.

## Problem Frame

The evaluator brief frames the deliverable as a system that tells the Director "where to focus their limited attention." Pre-#14, the dashboard was empty until you clicked the button — making the central proposition (a portfolio overview) invisible at first paint. Seed projects fix that: the moment the page loads, the evaluator sees the three projects, their PMs, and the known risks the system *should* surface in the report. The before/after comparison becomes meaningful because the "before" is now visible.

The full real-time provenance + conflict-detection + drill-down spec from the issue body was deferred deliberately — the AttentionFlag data model already carries `SourceAttribution` (landed in earlier work), but surfacing it interactively required UI work that #44/#45 ended up owning.

## Requirements Trace

- R1. **DONE** — Three seed projects (Phoenix, Omicron, DivatKirály) with `name`, `pm`, `team_size`, `qbr_date`, `q3_focus`, `known_risks`, `email_threads`, `team` (list of name+role), `health="unknown"` (`src/qbr/seed.py:get_demo_projects`).
- R2. **DONE** — Idempotent: `get_demo_projects()` is a pure function returning a fresh list on each call; running the CLI twice never duplicates.
- R3. **DONE** — Landing page renders one card per project with a coloured left border keyed on `health` (`src/qbr_web/templates/index.html` Portfolio Overview block).
- R4. **DONE** — `qbr seed-demo` CLI command prints each project as a Rich table (`src/qbr/cli.py:seed_demo`).
- R5. **DONE** — "Process Demo Emails" CTA copy reframed from "Run Demo (18 sample emails)" to "Process Demo Emails (18 threads, 3 projects)" with subhead "Watch the system process each email thread in real-time and see the dashboard update with Attention Flags."
- R6. **DEFERRED** — Real-time per-thread dashboard updates, provenance with conflict-detection UI, and drill-down pages were carried into #44/#45.

## Scope Boundaries

- **No real-time updates yet.** Cards stay at `health="unknown"` until #44/#45 wire up the live-update polling layer.
- **No drill-down route.** `/projects/{name}` is a #44/#45 deliverable.
- **No conflict-detection UI.** The data model supports source attribution and the `Conflict` shape, but rendering them interactively is out of scope here.
- **No persistence.** Seed data is a Python function called per request; there is no DB, no migrations, no idempotency tracking — re-rendering is the only "running it twice."
- **`get_seed_timestamp()` is a vestige.** Defined alongside `get_demo_projects` but unused by the web layer; harmless and left in place rather than ripped out in a follow-up.

## Context & Research

### Relevant Code and Patterns

- `task/sample_data/Colleagues.txt` — the source of truth for team rosters; the seed data is the curated subset of those names that map to the three projects.
- `src/qbr_web/app.py:index` (pre-#14) — already had a 2-column "Run Analysis" + "Recent Jobs" grid; #14 inserted the Portfolio Overview row above it without restructuring the existing layout.
- `src/qbr_web/templates/index.html` (pre-#14) — the template the dashboard cards were grafted onto. The `{% if has_sample_data %}` gate around the demo button stayed; the new Portfolio Overview block sits above it.
- `src/qbr/cli.py:seed_demo` (pre-#14) — was a stub that printed `"Demo seeding not yet implemented (issue #14)."` This issue closed the stub.
- `src/qbr/models.py:AttentionFlag`, `SourceAttribution` — already had the provenance fields. The deferred parts of the issue did not require model changes; the gap was UI.

## Key Technical Decisions

- **Seed data is a Python function, not JSON/YAML.** Rationale: it's static, it's small, and shipping it as code lets the CLI and web layer share a single import. No file I/O, no parsing, no environment differences. Trade-off: editing the demo set is a code change — acceptable for a fixture.
- **`health="unknown"` for every seed entry.** Rationale: seed data is the "before" state; flipping it to `good` would lie about what the system actually knows pre-analysis. The template's left-border colour map gives `unknown` a neutral grey border so the cards visibly say "we have no signal yet."
- **Cards use a left-border accent, not a full background.** Rationale: matches the visual idiom used elsewhere in the report sidebar (severity dots + restrained colour). Keeps the dashboard scannable when health changes from `unknown` → `critical`.
- **Team list in a `<details>` disclosure, not always-visible.** Rationale: team rosters are reference info; pulling them out of the default view keeps the card compact (PM, QBR date, focus, known risks) while keeping the data one click away. (Note: this `<details>` placement inside the future drill-down link becomes a problem #44/#45 has to fix — interactive elements can't legally nest inside `<a>`.)
- **Known risks rendered as an orange-tinted callout.** Rationale: pre-loaded risks are the system's hypothesis about what the analysis should confirm; visually distinguishing them from the rest of the card primes the evaluator to look for the connection in the final report.
- **Replace CTA copy, don't add a new button.** Rationale: the existing "Run Demo" button worked, but the framing was generic. "Process Demo Emails (18 threads, 3 projects)" + the live-update subhead sets the right expectation that the dashboard will *update*, not just produce a report.

## Implementation Units

- [x] **Unit 1: `src/qbr/seed.py` — demo project fixture**
  - **Goal:** Single source of truth for the three demo projects, importable from both the CLI and the web layer.
  - **Files:** `src/qbr/seed.py`.
  - **Approach:** New module with `get_demo_projects() -> list[dict]` returning three project dicts. Each dict has `name`, `pm` (formatted as `"Name (email)"`), `team_size`, `status="active"`, `health="unknown"`, `qbr_date`, `q3_focus`, `known_risks`, `email_threads`, and `team` (list of `{name, role}`). Names + roles are pulled from `task/sample_data/Colleagues.txt` so the fixture stays consistent with the email senders the parser will encounter. Hungarian names with diacritics preserved (`Péter`, `Gábor`, `Zsuzsa`, etc.) — the templates render them via Jinja autoescape so encoding is a non-issue.
  - **Verification:** Importable from both `qbr.cli` and `qbr_web.app`; calling it twice returns equivalent data with no shared mutable state.

- [x] **Unit 2: `qbr seed-demo` CLI command**
  - **Goal:** Replace the stub command with a Rich-table dump of `get_demo_projects()` output so the seed data can be inspected without launching the web app.
  - **Files:** `src/qbr/cli.py`.
  - **Approach:** Replace the existing `console.print("[yellow]Demo seeding not yet implemented (issue #14).[/yellow]")` body with: import `get_demo_projects`, print a header, then iterate the projects and build a `Table(title=proj["name"])` with rows for PM, Team size, QBR date, Q3 focus, Known risks, Email threads. Print blank line between projects.
  - **Verification:** `uv run qbr seed-demo` prints three labelled Rich tables matching the seed data; covered by the `make smoke-test` flow that exercises CLI commands end-to-end.

- [x] **Unit 3: Wire seed data into the web `index` route**
  - **Goal:** Pass `get_demo_projects()` into the index template context so the Portfolio Overview block can render.
  - **Files:** `src/qbr_web/app.py`.
  - **Approach:** Import `get_demo_projects` from `qbr.seed`, then in the `index` handler add `"projects": get_demo_projects()` to the template context alongside the existing `jobs` and `has_sample_data` keys. Two-line change at this stage; later PRs (#44/#45) overlay live `project_state` on top of the same context key.
  - **Verification:** `GET /` returns HTML containing each project name; `tests/test_web.py::TestIndex` continues to pass (its assertions are loose enough to survive the additions).

- [x] **Unit 4: Portfolio Overview cards in `index.html`**
  - **Goal:** Render a 3-column responsive grid of project cards above the existing "Run Analysis" + "Recent Jobs" row.
  - **Files:** `src/qbr_web/templates/index.html`.
  - **Approach:** New `Portfolio Overview` section at the top of the template. `{% for proj in projects %}` over the new context key, each card a `bg-white rounded-lg shadow p-4` with a `border-l-4` whose colour is conditioned on `proj.health` (`critical`/`warning`/`good`/else `gray-300`). Header row contains the project name + a small "Pending analysis" pill (rendered when `health=='unknown'`). Body shows PM (split on `(` to drop the email part), team size, QBR date, and a 80-char-truncated Q3 focus. Known-risks block renders inside an orange-tinted `<div>` if present. A `<details>` element at the bottom toggles the full team list. The layout uses `grid-cols-1 md:grid-cols-3` so the row collapses on mobile.
  - **Test scenarios:** `tests/test_web.py::TestIndex::test_index_loads` (existing test, lightly adjusted to assert "Process Demo Emails" instead of "Run Demo" — the only test diff in this PR); manual verification that the three cards render with their data in the right places.

- [x] **Unit 5: CTA copy + How-It-Works renumbering**
  - **Goal:** Refresh the affordances + explainer to match the dashboard framing.
  - **Files:** `src/qbr_web/templates/index.html`.
  - **Approach:** Demo-button label changes from "Run Demo (18 sample emails)" to "Process Demo Emails (18 threads, 3 projects)"; subhead changes to "Watch the system process each email thread in real-time and see the dashboard update with Attention Flags." Recent-Jobs heading renames to "Analysis History" (matches the dashboard register). The 4-step "How It Works" block gains numbered prefixes ("1. Parse" → "4. Report") and the per-step blurbs are reworded to mention source quotes (Extract) and recommended Director actions (Report).
  - **Verification:** Test assertion for the new CTA passes; visual inspection on the dashboard.

## System-Wide Impact

- **`projects` context key on `GET /`.** New contract — once #44/#45 layers live state on top, that work *merges* `project_state` into the same key rather than introducing a parallel one. This keeps the template surface stable.
- **`get_demo_projects()` becomes the canonical seed set.** Used by `_build_projects_state_payload()` later (#44/#45) to enumerate which project cards exist. Adding/removing a project here is a cross-surface change.
- **CLI behaviour change.** `qbr seed-demo` was a no-op stub; it now prints data. Any harness that grepped the old "[yellow]…not yet implemented" string breaks (none in-repo do).
- **`get_seed_timestamp()`** ships in `seed.py` but is unused — flagged here so a future cleanup pass can consider removing it. Keeping it has zero cost; removing it is a one-line PR.
- **Template restructure is additive, not destructive.** The pre-existing 2-column row stayed; the Portfolio Overview row is *prepended*. This means PR #24's snapshot tests + the broader `TestIndex` suite kept passing with one minor copy update.

## Sources & References

- GitHub issue: #14 — "Demo seed data + real-time processing dashboard + provenance tracking"
- Shipping commit: `e4d4f97` (PR #27)
- Files:
  - `src/qbr/seed.py` — `get_demo_projects`, `get_seed_timestamp` (unused)
  - `src/qbr/cli.py` — `seed_demo` command
  - `src/qbr_web/app.py` — `index` handler context (the `projects` key wire-in)
  - `src/qbr_web/templates/index.html` — Portfolio Overview block + CTA copy
  - `tests/test_web.py` — adjusted assertion for the new CTA copy
- Upstream: #11 (web app skeleton + landing page)
- Downstream: #44 / #45 (live dashboard polling, drill-down, real-time card updates) — see `docs/plans/2026-04-16-001-feat-live-project-dashboard-plan.md`
