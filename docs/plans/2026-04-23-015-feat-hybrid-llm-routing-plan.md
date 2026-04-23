---
title: "feat: Hybrid LLM routing — Ollama extraction + Anthropic synthesis (per-stage providers)"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #28"
shipped_in: "PR #29 (commit 7a5cff3); later extended by PR #49 (commit 2b96a41) which added the `claude-cli` provider and `FallbackClient` abstraction"
---

# feat: Hybrid LLM routing — Ollama extraction + Anthropic synthesis (per-stage providers)

## Overview

Replace the single-provider `LLMClient` lifecycle with a **per-stage** routing model. Extraction (Stage A/B/C — high-volume, runs once per email thread) goes to one provider; synthesis (Stage 4 — single report-generation call) goes to another. Both clients share a `UsageTracker` so cost roll-ups remain a single number.

The default stays "all-Ollama" (no API key required), but if `ANTHROPIC_API_KEY` is present and both stage providers were left at the default, synthesis auto-upgrades to Anthropic Sonnet 4.6 — matching the Blueprint's Haiku/Sonnet tier split with Ollama in the Haiku slot for zero-cost extraction.

## Problem Frame

Before this change, `src/qbr/cli.py` and `src/qbr_web/app.py` each created **one** `LLMClient` from `QBR_LLM_PROVIDER` and reused it for every call — extraction (~18 thread-scoped calls per demo run) and synthesis (1 call) both went to the same backend. That forced the user to choose between two bad ends:

- All-Ollama: free, but synthesis quality (the only call the user actually reads) is bound by a 8B local model.
- All-Anthropic: high quality everywhere, but ~18× the API cost vs. needing it only for the final synthesis.

The Blueprint already justified a Haiku-extraction / Sonnet-synthesis split. Issue #28 asked for the **same split with Ollama in the Haiku slot** so the demo can run at zero marginal cost while still producing a Sonnet-quality QBR report when an API key is available.

A second, equally important framing from the issue body: a Claude.ai subscription (`sk-ant-oat01-…`) is **not** API access (`sk-ant-api03-…`) — separate billing. The README needed to surface that so users do not waste a day debugging auth.

## Requirements Trace

- **R1** — A single pipeline run can use Ollama for extraction (Stage A/B/C) and Anthropic for synthesis (Stage 4). DONE — `create_hybrid_clients` returns `(extraction_client, extraction_model, synthesis_client, synthesis_model)` and both CLI + web app pass the right one to each stage.
- **R2** — Provider routing is configurable per stage, not just globally. DONE — `QBR_EXTRACTION_PROVIDER` and `QBR_SYNTHESIS_PROVIDER` env vars, falling back to `QBR_LLM_PROVIDER` when unset.
- **R3** — Works without `ANTHROPIC_API_KEY` (Ollama-only mode = current behavior preserved). DONE — default args of `create_hybrid_clients` are both `"ollama"`; auto-upgrade only fires when `api_key` is truthy.
- **R4** — Works with `ANTHROPIC_API_KEY` (hybrid or cloud-only). DONE — `test_explicit_providers` and `test_all_anthropic` cover those modes.
- **R5** — Clients share a single `UsageTracker` so cost summaries roll up uniformly. DONE — `test_shared_tracker` asserts identity.
- **R6** — `.env.example` documents the new vars **and** the Claude-subscription-vs-API distinction. DONE — see the diff to `.env.example`.
- **R7** — CLI surfaces "Hybrid mode" when the two stage providers differ. DONE — `_print_banner`-adjacent block in `src/qbr/cli.py` prints the cyan banner when `extraction_provider != synthesis_provider`.

## Scope Boundaries

- No LiteLLM / unified abstraction layer — the issue listed it as "consider", not required, and the two-provider matrix is small enough that a thin factory beats a dependency.
- No third provider in this PR. (The `claude-cli` provider was added later in PR #49 — see "System-Wide Impact" for lineage.)
- No async LLM clients. The Ollama and Anthropic SDKs are called from sync helpers wrapped in `asyncio.to_thread` at the web layer; that pre-existing pattern is reused as-is.
- No per-stage **model** override beyond what the provider implies — extraction always gets Haiku for Anthropic / `OLLAMA_MODEL` for Ollama; synthesis always gets Sonnet / `OLLAMA_MODEL`. A future PR can add `QBR_EXTRACTION_MODEL` / `QBR_SYNTHESIS_MODEL` if needed.
- No retry / cross-provider fallback in PR #29. (Added in PR #49 as `FallbackClient`.)

## Context & Research

### Relevant Code and Patterns

- `src/qbr/llm.py` — already had `LLMClient` ABC + `AnthropicClient` + `OllamaClient` + `create_client(provider=…)` factory. The new `create_hybrid_clients` is a thin wrapper that calls `create_client` twice and resolves the right model name per provider.
- `src/qbr/cli.py` `run` command — single point where extraction client is passed to `run_pipeline_for_thread` and synthesis client is passed to `generate_report`. Both call sites get the right client out of the new hybrid tuple.
- `src/qbr_web/app.py` `_run_analysis` — same shape as the CLI; the per-thread loop uses `extraction_client` and the final `generate_report` call uses `synthesis_client`.
- `qbr.llm.HAIKU_MODEL` / `SONNET_MODEL` constants — already canonical; reused by `create_hybrid_clients` for model-name resolution.
- `UsageTracker.summary()` — already aggregates across all `record()` calls regardless of which client wrote them. Sharing the tracker is sufficient for unified cost reporting.

## Key Technical Decisions

- **Tuple return shape `(client, model, client, model)`** rather than a dataclass. Rationale: the call sites are 2 (CLI + web), each unpacking inline. A tuple avoids importing a new type at every call site and matches the existing `create_client` style.
- **Auto-upgrade is opt-out by silence, not opt-in by flag.** If `ANTHROPIC_API_KEY` is set AND both per-stage providers are still `"ollama"` (i.e. user did not explicitly override), synthesis flips to Anthropic. Rationale: the user clearly opted in to Anthropic by setting the key; the most useful default is "use it where it matters most" (synthesis). An explicit `QBR_SYNTHESIS_PROVIDER=ollama` overrides.
- **Per-stage env vars fall back to `QBR_LLM_PROVIDER`** when unset. Rationale: existing single-provider deployments (the only mode that existed before) keep working without any config change. Hybrid mode is purely additive.
- **CLI prints the "Hybrid mode" banner only when providers differ** — not on all-Ollama or all-Anthropic. Rationale: the banner is a signal to the user that two backends are in play and cost split applies; on uniform setups it would be visual noise.
- **Default `OLLAMA_MODEL` flipped from `llama3.1:8b` to `gemma4:e2b`** in this commit. Rationale: gemma4:e2b extracts JSON-schema-constrained output more reliably on the small-laptop demo target. (`llama3.1:8b` remains a valid override.)

## Implementation Units

- [x] **Unit 1 — `create_hybrid_clients()` factory in `src/qbr/llm.py`**

  **Goal:** Add a single new entry point that returns two configured `LLMClient`s plus their resolved model names, sharing one `UsageTracker`.

  **Files:**
  - `src/qbr/llm.py`

  **Approach:**
  - New function `create_hybrid_clients(extraction_provider, synthesis_provider, api_key, ollama_host, ollama_model, tracker)` returning `tuple[LLMClient, str, LLMClient, str]`.
  - Internally calls the existing `create_client` twice with the shared tracker.
  - Auto-upgrade rule applied at the top: if `api_key and synthesis_provider == "ollama" and extraction_provider == "ollama"`, set `synthesis_provider = "anthropic"`.
  - Model-name resolution: `ollama_model if provider == "ollama" else HAIKU_MODEL` (extraction) / `SONNET_MODEL` (synthesis).

  **Test scenarios (in `tests/test_hybrid.py`):**
  - `TestHybridClients::test_all_ollama_default` — no API key → both clients Ollama, both models `gemma4:e2b`.
  - `TestHybridClients::test_auto_upgrade_with_api_key` — API key + both providers ollama → synthesis flips to Anthropic Sonnet, extraction stays Ollama.
  - `TestHybridClients::test_explicit_providers` — explicit `synthesis_provider="anthropic"` is respected even alongside Ollama extraction.
  - `TestHybridClients::test_all_anthropic` — both `"anthropic"` → Haiku extraction, Sonnet synthesis.
  - `TestHybridClients::test_shared_tracker` — passed tracker is the same identity on both returned clients.

- [x] **Unit 2 — Wire `src/qbr/cli.py` to consume the hybrid tuple**

  **Goal:** Replace the single-`client` flow with hybrid clients; keep all existing flags and Rich output working.

  **Files:**
  - `src/qbr/cli.py`

  **Approach:**
  - Read `QBR_EXTRACTION_PROVIDER` and `QBR_SYNTHESIS_PROVIDER`, defaulting each to the resolved `provider` (so `QBR_LLM_PROVIDER` continues to work as a global fallback).
  - Call `create_hybrid_clients(...)` and unpack into `extraction_client, extraction_model, synthesis_client, synthesis_model`.
  - Remove the old `if provider == "ollama": ... else: HAIKU_MODEL/SONNET_MODEL` resolution block — `create_hybrid_clients` owns that now.
  - Print a cyan `Hybrid mode: extraction=… synthesis=…` line when the two stage providers differ.
  - Pass `extraction_client` to `run_pipeline_for_thread` and `synthesis_client` to `generate_report`.
  - `smoke_test` continues to use the simpler `create_client` — single-provider check, no need for hybrid.

  **Test scenarios:**
  - Covered transitively by `tests/test_hybrid.py` for the factory; CLI wiring is exercised by the existing `make smoke-test` happy path.

- [x] **Unit 3 — Wire `src/qbr_web/app.py` `_run_analysis` to consume the hybrid tuple**

  **Goal:** Same shape change inside the FastAPI background task; no behavioural change for users running the web UI without the new env vars.

  **Files:**
  - `src/qbr_web/app.py`

  **Approach:**
  - Replace the local `create_client(...)` call inside `_run_analysis` with `create_hybrid_clients(...)`.
  - Read `QBR_EXTRACTION_PROVIDER` / `QBR_SYNTHESIS_PROVIDER` with fallback to `QBR_LLM_PROVIDER`.
  - Drop the local `if provider == "ollama": …` model-resolution block.
  - Per-thread `run_pipeline_for_thread` call switches to `extraction_client` + `extraction_model`.
  - Final `generate_report` call switches to `synthesis_client` + `synthesis_model`.
  - Default `OLLAMA_MODEL` flipped to `gemma4:e2b` to match the new CLI default.

  **Test scenarios:**
  - Existing `tests/test_web.py::TestAnalyze::test_start_demo_analysis_redirects` continues to pass — the change is internal to the background task, the HTTP surface is unchanged.

- [x] **Unit 4 — Update `.env.example` with hybrid config + Claude-subscription warning**

  **Goal:** Make the new env vars discoverable and prevent the "I have a Claude subscription, why doesn't it work?" support thread.

  **Files:**
  - `.env.example`

  **Approach:**
  - Group LLM config under a clear section header.
  - Document `QBR_LLM_PROVIDER` (global default) + the two new per-stage overrides as commented examples (so the file still parses with no change in behaviour out of the box).
  - Add the explicit warning: `Claude.ai subscription ($20/mo) does NOT provide API access — API billing is separate at console.anthropic.com`.
  - Link to https://console.anthropic.com for the API-key flow.
  - Keep `OLLAMA_HOST` + `OLLAMA_MODEL=gemma4:e2b` as the default-on path.

  **Test scenarios:** N/A — config-only; verified by reading `.env.example` after the diff.

## System-Wide Impact

- **Call-site contract:** `run_pipeline_for_thread` and `generate_report` were already client-agnostic (they accept any `LLMClient`). No signature change there — only callers swap which client they pass.
- **`UsageTracker.summary()` semantics unchanged:** still one rolled-up cost number per run, even though two distinct clients write to it. Anthropic-only calls cost-account against `HAIKU_MODEL`/`SONNET_MODEL` per existing pricing table; Ollama calls account at $0 (no entry in the pricing dict, falls through to default — but with `input_tokens=prompt_eval_count`, Ollama-only runs still report a "cost" against the fallback rate). NOTE: the Ollama cost-as-zero behaviour is implicit; a follow-up could short-circuit `estimated_cost_usd` for Ollama-model entries.
- **Backward compatibility:** every prior single-provider configuration keeps working unchanged. New users see `gemma4:e2b` as the default Ollama model (was `llama3.1:8b`); README must show the new model.
- **Test surface:** new `tests/test_hybrid.py` covers the factory in isolation. Existing `tests/test_web.py` and CLI tests remain green because the public HTTP and CLI surfaces are unchanged.

### Lineage — extension in PR #49 (commit 2b96a41)

PR #49 built on this plan's foundations:
- Added a third provider, `ClaudeCLIClient`, that shells out to the Claude Code CLI (`claude -p`) so the user's OAuth subscription can be used without an API key.
- Added `FallbackClient`, a wrapper that catches exceptions from a primary client and routes the call to a secondary. `create_hybrid_clients` now wraps any `claude-cli` side in a `FallbackClient` whose secondary is a local Ollama call (same `OLLAMA_MODEL`).
- Added env vars `QBR_CLAUDE_CLI_MODEL` (default `opus`) and `QBR_CLAUDE_CLI_TIMEOUT_S` (default `60`).
- `_run_analysis` logs `Using Claude opus via CLI (OAuth subscription, timeout 60s, fallback: gemma4:e2b)` at run start when either stage is `claude-cli`, and appends a `(timeout Xs, fallback: …)` hint to the per-thread `Extracting with …` line.

That extension was deliberately layered **on top of** the per-stage tuple shape introduced here — `create_hybrid_clients` returns the same `(client, model, client, model)` tuple, so the call sites in `cli.py` and `app.py` did not need to change again.

## Sources & References

- GitHub issue: [#28 — Hybrid LLM extraction + synthesis](https://github.com/peterkolcza/attrecto-qbr-task/issues/28)
- Pull request (this plan): [#29](https://github.com/peterkolcza/attrecto-qbr-task/pull/29) — commit `7a5cff3`
- Follow-on PR (Claude CLI provider + fallback): [#49](https://github.com/peterkolcza/attrecto-qbr-task/pull/49) — commit `2b96a41`
- Code touched (PR #29):
  - `src/qbr/llm.py` — `create_hybrid_clients` added
  - `src/qbr/cli.py` — `run` rewired to hybrid tuple, "Hybrid mode" banner
  - `src/qbr_web/app.py` — `_run_analysis` rewired to hybrid tuple
  - `.env.example` — new section + Claude-subscription note
- Tests: `tests/test_hybrid.py` (5 cases — see Unit 1).
- External: [Anthropic console](https://console.anthropic.com), [Anthropic pricing](https://www.anthropic.com/pricing), [Ollama Python lib](https://github.com/ollama/ollama-python).
- Related design doc: `Blueprint.md` cost-management section (Haiku/Sonnet tier split that this plan instantiates with Ollama in the Haiku slot).
