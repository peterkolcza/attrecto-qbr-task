---
title: "docs: README.md — setup, usage, and AI model justification"
type: docs
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #10"
shipped_in: "PR #23 (commit 6ac9c06); refined by commit 47b0003 (gemma4 default + detailed prerequisites); refreshed by PR #52 (commit e99247a, claude-cli + live dashboard)"
---

# docs: README.md — setup, usage, and AI model justification

## Overview

`README.md` is the entry point for evaluators and any new contributor: prerequisites, install, configuration, CLI + web usage, model-choice justification, project structure, and deployment notes. The brief grades it on whether a fresh user can follow it to a working run, and on whether the AI model choices are explained with rationale (not just listed).

The initial commit (`6ac9c06`, PR #23) shipped the comprehensive structure (Quick Start, CLI usage, architecture overview, model justification, project structure, links to Blueprint). Commit `47b0003` then changed the default provider to Ollama + `gemma4:e2b` (no API key required) and expanded prerequisites, model-by-RAM recommendations, and deployment quick reference. PR #52 (commit `e99247a`) brought the README in sync with the code that shipped after the initial deliverable: the `claude-cli` OAuth-subscription provider, `FallbackClient`, the live dashboard, and per-stage provider split.

## Problem Frame

Issue #10 asks for setup instructions, usage examples, and model-choice rationale (why Anthropic, why the Haiku/Sonnet split, why an Ollama fallback). Without this, the deliverable is a black box — an evaluator who clones the repo can't get to a working run, and a reader has no story for why the model lineup was chosen.

The default-provider switch in `47b0003` reflected a real concern: the default `make run` should not require an API key, otherwise the very first thing an evaluator tries fails. PR #52's refresh closed the gap between what the README documented and what the code actually offered (three providers, fallback, dashboard).

## Requirements Trace

- R1. DONE — Setup: Prerequisites table + Installation steps cover `uv sync`, model pull (`ollama pull gemma4:e2b`), `.env` configuration, and a Verify step (`make test`, `make smoke-test`). Headings at `README.md:5`, `:16`, `:36`.
- R2. DONE — Usage: CLI section with `make run` example, expected output, `--debug` mode, full CLI option list, and other commands. Web UI section documents `make web`. Headings at `README.md:46`, `:48`, `:97`, `:109`, `:116`.
- R3. DONE — Model justification: dedicated "AI Model Choices & Justification" section explains the default Ollama + gemma4 choice, the Anthropic Claude alternative (Haiku 4.5 extraction + Sonnet 4.6 synthesis), the claude-cli OAuth-subscription path (added in PR #52), and the provider-agnostic design. Headings at `README.md:187`, `:189`, `:197`, `:205`, `:214`.
- R4. DONE — Configuration section enumerates env vars for all three providers (`ollama` / `anthropic` / `claude-cli`), per-stage overrides (`QBR_EXTRACTION_PROVIDER`, `QBR_SYNTHESIS_PROVIDER`), and the auth vars (`QBR_AUTH_*`, `QBR_SESSION_SECRET`). Heading at `README.md:128`.
- R5. DONE — Links to `Blueprint.md` from the Architecture section and from the Deliverables section. The deployed UI is documented in the Deployment section with the live dashboard / drill-down subsection added in PR #52. Headings at `README.md:167`, `:249`, `:267`, `:282`.

## Scope Boundaries

- No tutorial content beyond the Quick Start — for design rationale the README links to `Blueprint.md` rather than duplicating it.
- No API reference for `qbr` Python modules — the codebase is small and `cli.py` is self-documenting via Typer.
- No screenshots in the README — the demo flow is one `make web` away and screenshots go stale.
- No CI/CD documentation in the README — the GitHub Actions workflow is its own concern.
- No FAQ / troubleshooting catalogue — the prerequisites + verification steps cover the common failure modes.

## Context & Research

### Relevant Code and Patterns

- `Makefile` — `install`, `lint`, `test`, `run`, `run-debug`, `smoke-test`, `web` targets are the contract the README's Quick Start relies on.
- `src/qbr/cli.py` — `qbr run`, `qbr smoke-test`, `qbr hash-password`, `qbr seed-demo` subcommands are documented in the Usage and Configuration sections.
- `src/qbr/llm.py` — three providers (`anthropic`, `ollama`, `claude-cli`) and the `FallbackClient`; the Configuration section's env-var block mirrors what this module reads.
- `.env.example`, `.env.prod.example` — kept in lockstep with the README's Configuration block (commit `47b0003` updated all three together).
- `Blueprint.md` — linked from Architecture and Deliverables; the README intentionally stays high-level so the Blueprint is the single source for design rationale.

## Key Technical Decisions

- **Default to Ollama + gemma4:e2b, not Anthropic.** A first-time evaluator should be able to clone, `make install`, `ollama pull gemma4:e2b`, `make run` and get output without setting up billing. Made in commit `47b0003`. The Anthropic and `claude-cli` paths are documented as alternatives.
- **Model justification is a dedicated section, not scattered.** The brief explicitly asks for "justification of the AI models chosen". Putting it under one heading lets the evaluator find and grade it directly.
- **Prerequisites as a table, not a wall of prose.** Tools + minimum versions + install hint per row reads faster than paragraphs and makes failures self-diagnosable.
- **One Configuration code block listing all env vars.** Consolidating into a single `.env` template (matching `.env.example`) is more useful than spreading config across the doc.
- **Live dashboard subsection added post-hoc in PR #52.** The dashboard / drill-down (PRs #46, #47) shipped without README coverage; PR #52 closed that gap rather than letting the README drift.

## Implementation Units

- [x] **Unit 1: Prerequisites + Installation + Verify**

  **Goal:** A new user can go from `git clone` to a successful `make smoke-test` by following these three sections in order.

  **Files:**
  - `README.md` (`## Prerequisites`, `## Installation`, `### Verify installation`)

  **Approach:** Prerequisites table lists Python 3.11+, `uv`, Ollama (or an Anthropic API key), and Make with install hints. Installation walks through clone → `uv sync --all-extras` → `ollama pull gemma4:e2b` → copy `.env`. Verify step runs `make test` and `make smoke-test`.

  **Verification:** Headings present at `README.md:5`, `:16`, `:36`. Steps match the actual `Makefile` targets and `.env.example`.

- [x] **Unit 2: Usage — CLI + Web UI**

  **Goal:** Show what running the system looks like from both the CLI and the web UI, with example output and the full option list.

  **Files:**
  - `README.md` (`## Usage`, `### CLI — Generate a Portfolio Health Report`, `### CLI Options`, `### Other commands`, `### Web UI`)

  **Approach:** CLI subsection shows `qbr run --input task/sample_data --output reports/`, the `make run` shorthand, `--debug` mode, and a sample of the rich progress output. Options list mirrors the Typer-defined flags. Other commands lists `qbr smoke-test`, `qbr hash-password`, `qbr seed-demo`. Web UI subsection documents `make web` and the `:8000` URL.

  **Verification:** Headings present at `README.md:46`, `:48`, `:97`, `:109`, `:116`. Commands match `src/qbr/cli.py`.

- [x] **Unit 3: Configuration — env vars for three providers + auth**

  **Goal:** A single canonical block listing every env var the system reads, grouped by provider and concern.

  **Files:**
  - `README.md` (`## Configuration`, `### Recommended models by RAM`)

  **Approach:** One code block annotated with comments grouping the three provider variants (`ollama` default, `anthropic` API, `claude-cli` OAuth subscription including `CLAUDE_CODE_OAUTH_TOKEN` for Docker / CI), per-stage overrides, and the auth vars (`QBR_AUTH_USER`, `QBR_AUTH_PASSWORD_HASH`, `QBR_SESSION_SECRET`). RAM-by-model table helps users pick the right local model.

  **Verification:** Heading at `README.md:128`. Block matches `src/qbr/llm.py` and `qbr_web/auth.py` env reads. RAM table at `:157`.

- [x] **Unit 4: AI Model Choices & Justification**

  **Goal:** Explain why the model lineup is what it is — the brief's explicit grading criterion.

  **Files:**
  - `README.md` (`## AI Model Choices & Justification`, `### Default: Ollama + Gemma 4`, `### Alternative: Anthropic Claude (API key)`, `### Alternative: Claude via CLI (OAuth subscription)`, `### Provider-agnostic design`)

  **Approach:** Default subsection explains why Ollama + gemma4 — local, no key, good enough for the PoC's structured-output workload. Anthropic alternative explains the Haiku 4.5 (extraction) + Sonnet 4.6 (synthesis) split, the prompt-caching benefit, and the cost rationale. claude-cli alternative (added in PR #52) explains the OAuth-subscription model — opus quality without per-call API spend, with the trade-offs called out (startup latency, estimated token counts because the CLI doesn't surface usage telemetry, automatic FallbackClient drop to Ollama on timeout). Provider-agnostic subsection notes per-stage split via `QBR_EXTRACTION_PROVIDER` / `QBR_SYNTHESIS_PROVIDER`.

  **Verification:** All four subsections present at `README.md:187`, `:189`, `:197`, `:205`, `:214`. Each variant explains the choice, not just lists it.

- [x] **Unit 5: Architecture, Deployment, Deliverables links**

  **Goal:** Orient the reader to the codebase shape, point at how the system is deployed, and link out to the design deliverables.

  **Files:**
  - `README.md` (`## Architecture`, `## Project Structure`, `## Development`, `## Deployment (Oracle VPS)`, `### Live dashboard & drill-down`, `## Deliverables`, `## Source Material`)

  **Approach:** Architecture section gives the high-level pipeline + links to `Blueprint.md`. Project Structure is a tree of `src/qbr/`, `src/qbr_web/`, `prompts/` (now `src/qbr/prompts/`), `tests/`. Deployment section covers the Oracle VPS quick path (PR #52 added the claude-cli + pre-installed CLI in Docker mention). Live dashboard subsection (added in PR #52) documents the dashboard features that shipped in PRs #46 / #47. Deliverables section links Blueprint.md and the example report.

  **Verification:** Headings at `README.md:167`, `:218`, `:239`, `:249`, `:267`, `:282`, `:288`. Links to Blueprint.md resolve.

## Sources & References

- GitHub issue: #10
- Pull requests / commits: PR #23 (commit `6ac9c06`, initial comprehensive README); commit `47b0003` (gemma4 default + detailed prerequisites and deployment); PR #52 (commit `e99247a`, claude-cli provider, FallbackClient, live dashboard).
- Related files: `Makefile`, `.env.example`, `.env.prod.example`, `src/qbr/cli.py`, `src/qbr/llm.py`, `Blueprint.md`.
