---
title: "feat: CLI entry point — end-to-end pipeline with verbose output and debug mode"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #8"
shipped_in: "PR #21 (commit dba9336)"
---

# feat: CLI entry point — end-to-end pipeline with verbose output and debug mode

## Overview

Typer-based CLI that wires the full QBR pipeline together: parse → extract (Haiku) → classify flags → synthesize report (Sonnet) → save Markdown + JSON. Default mode prints a startup tech banner, per-step `rich` progress, and a closing token-usage / cost table. `--debug` flips logging to DEBUG so every prompt and LLM response is traced to stderr, and dumps the rendered Markdown report to stdout in a `Panel`.

A `smoke-test` subcommand exists for quick provider verification (used by `make smoke-test` and `scripts/smoke.sh` against Ollama, no API key needed). Two operational subcommands — `hash-password` and `seed-demo` — were added alongside as small QoL helpers.

## Problem Frame

Issue #8 framed this as the wiring step: "Typer CLI that wires the full pipeline together with rich verbose output and debug mode." Until this landed, the modules from issues #2–#7 were callable individually but there was no single entry point that produced the graded artefact (`reports/portfolio_<date>.md`). The verbose output is also part of the brief's "monitoring/transparency" story — the user should see what the system did and roughly what it cost.

## Requirements Trace

- R1. DONE — `qbr run --input task/sample_data --output reports/ --provider anthropic` runs the full pipeline (`src/qbr/cli.py:67`).
- R2. DONE — Startup banner prints provider, pipeline shape, security posture, caching status, debug state (`_print_banner`, `src/qbr/cli.py:35`).
- R3. DONE — `rich.Progress` step indicators for parse / extract / classify / report, with green checkmarks and per-project item/open counts after extraction.
- R4. DONE — Closing summary table reports total LLM calls, input tokens, output tokens, and estimated USD cost (`_print_usage_summary`, `src/qbr/cli.py:53`).
- R5. DONE — `--debug` enables DEBUG logging across `qbr.*` loggers so the LLM client traces full prompts + responses; debug also dumps the Markdown report inside a `rich.Panel` at the end.
- R6. DONE — `qbr smoke-test` runs a one-shot provider check (`src/qbr/cli.py:222`); `scripts/smoke.sh` calls it without needing an API key when configured for Ollama.
- R7. DONE — Provider auto-detection via `QBR_LLM_PROVIDER` env var; per-stage override via `QBR_EXTRACTION_PROVIDER` / `QBR_SYNTHESIS_PROVIDER` for hybrid runs (e.g. Haiku extraction + Sonnet synthesis); `.env` loaded via `python-dotenv` at import time.

## Scope Boundaries

- No interactive prompts / wizard mode — every option has an env or CLI default.
- No JSON-only output mode for the CLI itself; the pipeline already writes the JSON dashboard envelope to disk via `save_report`.
- No parallel thread processing — extractions run sequentially. Acceptable for the 18-thread sample; concurrency is a Blueprint-level scaling concern, not a PoC concern.
- No `--quiet` flag yet; the default is already minimal (rich progress + summary). Add later if needed.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/parser.py` — `parse_all_emails`, `parse_colleagues` provide the `Thread` list and `Colleague` roster.
- `src/qbr/pipeline.py` `run_pipeline_for_thread(thread, client, colleagues, extraction_model)` — the per-thread extract+resolve combo, returns `(items, metrics)`.
- `src/qbr/flags.py` `aggregate_flags_by_project` — pure post-processing on `dict[str, list[ExtractedItem]]`.
- `src/qbr/report.py` — `generate_report`, `build_report_json`, `save_report` (issue #7).
- `src/qbr/llm.py` — `create_hybrid_clients` returns `(extraction_client, extraction_model, synthesis_client, synthesis_model)` tuple, sharing one `UsageTracker`. `create_client` is the single-provider variant used by `smoke-test`.

## Key Technical Decisions

- **Typer over argparse / click.** Typer gives us decorators + automatic `--help` rendering with type annotations as docstrings; the surface area is small enough that the framework cost is negligible. Matches the existing project convention of using batteries-included libraries (FastAPI, Pydantic).
- **Logging level toggled by `--debug`, not a separate `--verbose`.** The default is already "show meaningful progress", so the only meaningful axis is "show me everything". Setting `qbr.*` loggers to WARNING in non-debug mode keeps the rich output clean.
- **Hybrid providers as first-class concept.** `QBR_EXTRACTION_PROVIDER` / `QBR_SYNTHESIS_PROVIDER` allow running cheap extraction on Ollama while keeping synthesis on Sonnet (or vice-versa). The CLI announces hybrid mode explicitly so the user isn't surprised by the cost / quality tradeoff.
- **Banner is decorative but informative.** It exists because the brief weights "transparency" — the user should see provider, pipeline stages, and security posture before any LLM call fires. Keeps the system explainable in a demo context.
- **Per-thread errors are logged and skipped, not fatal.** A single malformed email shouldn't abort an 18-email run. The `try/except` around `run_pipeline_for_thread` prints a yellow warning and continues; in `--debug` it also prints the full traceback.

## Implementation Units

- [x] **Unit 1: Typer app skeleton + banner + usage helpers**

  **Goal:** Set up `typer.Typer(name="qbr")`, `.env` loading, the rich `Console`, `_print_banner`, and `_print_usage_summary` shared by `run` and `smoke-test`.

  **Files:**
  - `src/qbr/cli.py` (top of file, `_print_banner`, `_print_usage_summary`)

  **Approach:** Single module-level `app`, `console`, `load_dotenv()` at import. Banner is a `rich.Panel.fit` with conditional rows for provider-specific lines (caching only meaningful on Anthropic).

  **Test scenarios:**
  - Verified manually via `make run` and `make run-debug` — banner renders, panel borders are cyan, debug toggle changes the "Debug:" line.

- [x] **Unit 2: `qbr run` — full pipeline wiring**

  **Goal:** Resolve provider, build hybrid clients, parse, extract per thread, classify, synthesize, save. Print step-by-step progress and final paths.

  **Files:**
  - `src/qbr/cli.py` (`run`)

  **Approach:**
  - Resolve `provider` from CLI flag → env (`QBR_LLM_PROVIDER`) → default `"anthropic"`.
  - Set logging level (`logging.DEBUG` if `--debug`, else INFO; `qbr.*` clamped to WARNING when not debugging).
  - Call `create_hybrid_clients(...)` with optional per-stage env overrides.
  - Validate input directory; load colleagues if `Colleagues.txt` exists.
  - Step 1 (parse) → Step 2 (extract per-thread loop, swallowing per-thread exceptions with a yellow warning) → Step 3 (`aggregate_flags_by_project`) → Step 4 (`generate_report` + `build_report_json` + `save_report`).
  - Print closing usage table; in debug, dump the Markdown report inside a `Panel`.

  **Test scenarios:**
  - `make run` against `task/sample_data/` produces `reports/portfolio_<timestamp>.md` + `.json` and exits 0. (Verified during PR #21 review; no automated CLI test — pipeline coverage is in `tests/test_pipeline.py` / `tests/test_report.py`.)
  - `make run-debug` adds the full prompt/response trace to stderr and the report panel to stdout.

- [x] **Unit 3: `qbr smoke-test` — provider liveness check**

  **Goal:** One LLM call against the configured provider that asserts the response contains a known sentinel.

  **Files:**
  - `src/qbr/cli.py` (`smoke_test`)
  - `scripts/smoke.sh`, `Makefile` target `smoke-test`

  **Approach:** `create_client(...)`, ask for the literal string `QBR_SMOKE_TEST_OK`. Print green check on success, yellow on unexpected response, red + non-zero exit on exception. Closes with the same usage-summary table.

  **Test scenarios:**
  - `make smoke-test` against Ollama returns a green check without an API key.
  - On a missing/invalid key for the Anthropic provider it exits 1 with a red error line.

- [x] **Unit 4: Operational subcommands (`hash-password`, `seed-demo`)**

  **Goal:** Two small helpers attached to the same Typer app for ergonomics — generate a bcrypt hash for the auth env var, and dump the demo project seed used by the dashboard.

  **Files:**
  - `src/qbr/cli.py` (`hash_password`, `seed_demo`)

  **Approach:** `hash_password` calls `qbr_web.auth.hash_password` and prints the env-var line ready to paste. `seed_demo` calls `qbr.seed.get_demo_projects()` and prints one rich `Table` per project.

  **Test scenarios:**
  - `uv run qbr hash-password testpw` returns a bcrypt-shaped string and the env-var template line.
  - `uv run qbr seed-demo` prints three project tables matching `src/qbr/seed.py`.

## Sources & References

- GitHub issue: #8
- Pull request / commit: PR #21, commit `dba9336`
- Related code: `src/qbr/cli.py`, `src/qbr/pipeline.py`, `src/qbr/llm.py`, `src/qbr/report.py`, `src/qbr/flags.py`, `src/qbr/parser.py`
- Operational scripts: `scripts/smoke.sh`, `Makefile` (`run`, `run-debug`, `smoke-test`)
