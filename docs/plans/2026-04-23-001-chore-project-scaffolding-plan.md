---
title: "chore: Project scaffolding — pyproject, uv, ruff, module layout"
type: chore
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #1"
shipped_in: "PR #15 (commit 197f11c)"
---

# chore: Project scaffolding — pyproject, uv, ruff, module layout

## Overview

Set up the Python project skeleton so subsequent feature issues (#2–#11) had a stable home. This shipped the `pyproject.toml` (uv-managed, Python ≥3.12), the `src/qbr/` and `src/qbr_web/` package layouts, the Pydantic data-model contract used across the pipeline, the Typer CLI entry point, ruff config, a Makefile of standard tasks, and four scaffold smoke tests proving the skeleton imports cleanly.

## Problem Frame

From the issue body:

> Set up the Python project skeleton with all tooling.

There was no Python project yet — every downstream issue (parser, LLM client, pipeline, web UI) needed an agreed module layout, dependency set, and toolchain before work could begin. This unblocks the rest of the roadmap and locks in the model contract (Pydantic) so parser and LLM units can implement against shared types.

## Requirements Trace

- R1. `pyproject.toml` with `uv` lock and Python ≥ 3.12 — DONE.
- R2. Dependencies present: `anthropic`, `ollama`, `pydantic`, `python-dotenv`, `typer`, `rich`, `pytest`, `ruff` — DONE (web/dev split into optional-dependencies groups).
- R3. Layout `src/qbr/` with `parser`, `llm`, `pipeline`, `report`, `cli` modules plus `tests/` and `prompts/` — DONE (also `flags`, `security`, `models` placeholders for #4/#7/#8).
- R4. `.gitignore` covering `.env`, `.venv`, `__pycache__`, `reports/` — DONE.
- R5. `.env.example` listing `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, `QBR_LLM_PROVIDER` — DONE.
- R6. `ruff` config in `pyproject.toml` — DONE (E, F, W, I, UP, B, SIM, TCH).
- R7. `Makefile` with `install`, `lint`, `test`, `run` — DONE (also `format`, `run-debug`, `web`).
- R8. Verification: `uv sync && uv run pytest` green on empty/scaffold suite, `uv run ruff check` clean — DONE (4 scaffold tests passing).

## Scope Boundaries

- No real implementation in `parser.py`, `llm.py`, `pipeline.py`, `flags.py`, `report.py`, `security.py` — they shipped as one-line placeholders, deferred to issues #2–#8.
- No web UI logic in `src/qbr_web/` — package marker only, deferred to #11.
- No CI workflow file — local Makefile + ruff was enough for the PoC.
- No prompts under `prompts/` yet — `.gitkeep` only; populated as extraction/synthesis prompts land.

## Context & Research

### Relevant Code and Patterns

- `pyproject.toml` — single source of truth for deps, ruff config, pytest config (`pythonpath = ["src"]`), and the `qbr` console script entry point.
- `src/qbr/models.py` — Pydantic v2 contract shared across parser, pipeline, flags, report. Defines `Message`, `Thread`, `SourceAttribution`, `ExtractedItem`, `AttentionFlag`, `Conflict`, `Colleague`, plus the `Severity` / `FlagStatus` / `ItemType` / `SourceType` / `FlagType` / `ResolutionStatus` enums.
- `src/qbr/cli.py` — Typer app exposing `run`, `smoke-test`, `seed-demo` (skeleton only at this point).
- `Makefile` — standardised commands so contributors don't memorise `uv run ...` invocations.
- `tests/test_scaffold.py` — four import-level tests proving the package builds and modules are wired.

## Key Technical Decisions

- **`src/` layout, not flat package** — rules out accidental imports from CWD, forces `pythonpath = ["src"]` in pytest config, and matches modern Python packaging guidance.
- **Pydantic v2 model module up front** — rather than letting each downstream issue invent its own types, the data contract was agreed in this scaffold so parser output (`Thread`, `Message`) and LLM-extraction output (`ExtractedItem`) share a vocabulary with the flag engine.
- **uv over pip/poetry** — fast, lockfile-native, and the task brief allows any Python tool. `make install` is just `uv sync --all-extras`.
- **Ruff (lint + format) instead of black + isort + flake8** — single binary, single config block; the chosen rule selection (`E, F, W, I, UP, B, SIM, TCH`) covers style, imports, modernisation, bugbear, simplification, and type-checking-only imports without going overboard.
- **Optional-dependency groups (`dev`, `web`)** — keeps the core install minimal for CLI users; `make install` pulls everything via `--all-extras` for development.
- **Two packages (`qbr` and `qbr_web`) declared in `[tool.hatch.build.targets.wheel]`** — separates the library from the FastAPI app so the CLI can be installed/used independently.
- **Typer over argparse** — gives `qbr smoke-test` / `qbr run` / `qbr seed-demo` ergonomics for free and pairs naturally with `rich` console output.

## Implementation Units

- [x] **Unit 1: pyproject + tooling + Makefile**

  **Goal:** Stand up the dependency, lint, and task-runner contract.

  **Files:**
  - `pyproject.toml`
  - `Makefile`
  - `.gitignore`
  - `.env.example`

  **Approach:**
  - `pyproject.toml` declares Python ≥3.12, runtime deps (`anthropic`, `ollama`, `pydantic`, `python-dotenv`, `typer[all]`, `rich`), `dev` extras (pytest, ruff, pip-audit), `web` extras (fastapi, uvicorn, jinja2, sse-starlette, python-multipart). Ruff selects `E, F, W, I, UP, B, SIM, TCH` with `ignore = ["E501"]` and `line-length = 100`. Pytest config sets `pythonpath = ["src"]` so test discovery works without an editable install hop.
  - `Makefile` wraps `uv sync --all-extras` (install), `ruff check + format --check` (lint), `ruff check --fix + format` (format), `pytest -v` (test), CLI `qbr run`/`run-debug` (run pipeline on `task/sample_data/`), and `uvicorn` dev server (`web`).
  - `.gitignore` excludes `.env`, `.venv`, `__pycache__`, `reports/`, plus build/test artefacts.
  - `.env.example` lists the three configuration keys downstream code reads (`ANTHROPIC_API_KEY`, `OLLAMA_HOST`, `QBR_LLM_PROVIDER`).

  **Test scenarios:**
  - `tests/test_scaffold.py::test_version` confirms `qbr.__version__` is exposed and parses as a SemVer-shaped string.

- [x] **Unit 2: Module layout + Pydantic contract + Typer skeleton**

  **Goal:** Create the package directories that downstream issues will fill in, lock in the shared data model, and expose the `qbr` console entry point.

  **Files:**
  - `src/qbr/__init__.py`
  - `src/qbr/models.py`
  - `src/qbr/cli.py`
  - `src/qbr/parser.py`, `src/qbr/llm.py`, `src/qbr/pipeline.py`, `src/qbr/flags.py`, `src/qbr/report.py`, `src/qbr/security.py` (placeholders)
  - `src/qbr_web/__init__.py`
  - `prompts/.gitkeep`, `reports/.gitkeep`
  - `tests/__init__.py`, `tests/test_scaffold.py`

  **Approach:**
  - `src/qbr/models.py` defines the full Pydantic model surface used by the rest of the app: `Message` (with `is_off_topic` already declared so the parser in #2 can populate it), `Thread`, plus the LLM-extraction-and-flag pipeline contract (`ExtractedItem`, `AttentionFlag`, `SourceAttribution`, `Conflict`) and the supporting enums.
  - `src/qbr/cli.py` registers a Typer app with `run`, `smoke-test`, and `seed-demo` commands (bodies are placeholders; later issues fill them in). The console-script entry point (`qbr = "qbr.cli:app"`) makes `uv run qbr ...` work post-install.
  - All other `src/qbr/*.py` modules ship as one-line placeholders so imports succeed; their real implementations land in #2–#8.

  **Test scenarios:**
  - `tests/test_scaffold.py::test_models_import` instantiates the core Pydantic models with minimal data, proving the contract loads without mutual-import issues.
  - `tests/test_scaffold.py::test_cli_import` imports the Typer app and asserts `run`/`smoke-test`/`seed-demo` are registered commands.
  - `tests/test_scaffold.py::test_placeholder_modules_import` imports `parser`, `llm`, `pipeline`, `flags`, `report`, `security` so a future regression that breaks the package surface fails fast.

## Sources & References

- Issue: <https://github.com/peterkolcza/attrecto-qbr-task/issues/1>
- PR: <https://github.com/peterkolcza/attrecto-qbr-task/pull/15>
