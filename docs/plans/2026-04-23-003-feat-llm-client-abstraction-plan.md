---
title: "feat: LLM client abstraction with Anthropic + Ollama, prompt caching, token tracking"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #3"
shipped_in: "PR #17 (commit 053860e)"
---

# feat: LLM client abstraction with Anthropic + Ollama, prompt caching, token tracking

## Overview

Introduce a single `LLMClient` interface so the rest of the pipeline (extraction, classification, synthesis) can talk to either Anthropic Claude or a local Ollama model without branching. This shipped the abstract base class, the `AnthropicClient` (with `cache_control` prompt caching, structured-output via tool_use, retry-with-backoff), the `OllamaClient` (JSON mode for structured output), `TokenUsage`/`UsageTracker` for per-call accounting and cost estimation, and a `create_client` factory keyed by provider string. 14 fully-mocked tests cover the surface — no real API calls are made in CI.

This abstraction was deliberately built to be extended: PR #28 / #49 later added `ClaudeCLIClient` (Claude Code OAuth subscription provider) and `FallbackClient` (wraps a primary client and falls back to a local Ollama on failure), now visible in `src/qbr/llm.py` alongside the original three classes. Those landed after this issue but slot into the same interface unchanged.

## Problem Frame

From the issue body:

> Unified LLM client interface with Anthropic Claude (primary) and Ollama (fallback/dev) implementations.

The pipeline issues (#4 onwards) all need to call an LLM, but they should not care which provider is on the other end of the call — that decision belongs in configuration (`QBR_LLM_PROVIDER`) and in the cost/availability tradeoff. The interface also has to surface token usage (the task brief weighs cost-management) and support cache-control so that the Colleagues roster + system prompt aren't re-billed on every per-thread extraction call.

## Requirements Trace

- R1. `LLMClient` interface with `complete(system, messages, model, response_schema=None) -> str | dict` — DONE (abstract base in `src/qbr/llm.py`).
- R2. Anthropic implementation using `claude-haiku-4-5` for extraction, `claude-sonnet-4-6` for synthesis — DONE (model selected per call by the caller; the client doesn't lock either in).
- R3. Ollama implementation with the same interface for offline dev — DONE (`OllamaClient`, JSON mode for structured output).
- R4. Structured output that guarantees schema-valid JSON — DONE (Anthropic side uses `tool_use` with the response schema as the tool's input schema, which is the documented mechanism for forcing JSON shape; Ollama uses `format: "json"`).
- R5. Prompt caching: system prompt + roster live in the cached prefix via `cache_control: {"type": "ephemeral"}` — DONE (system block is wrapped with `cache_control` when caching is requested).
- R6. Retry with exponential backoff — DONE.
- R7. Token usage logging: per-call counts, cost estimate, cumulative totals — DONE (`TokenUsage` Pydantic model + `UsageTracker` accumulator). Cost estimates use the published Haiku 4.5 / Sonnet 4.6 pricing from the issue body.
- R8. `--smoke-test` CLI command for provider verification — DONE (Typer command on `src/qbr/cli.py`; the body was scaffolded in #1 and wired to `create_client(...).complete(...)` here).

Batch API support and an explicit cost discount calculator were captured as design intent in the cost-model table but were not implemented as code in this issue — Batch API is a Blueprint-level cost-management lever, not part of the runtime PoC.

## Scope Boundaries

- No batch API client — the cost table in the issue documents the discount but the PoC sticks to synchronous calls. Batch is a Blueprint scaling lever.
- No streaming — `complete` returns once the response is ready. Streaming would complicate the structured-output contract and adds nothing for non-interactive analysis.
- No automatic model routing (Haiku vs Sonnet) inside the client — the caller picks per call. Routing logic belongs in the pipeline, not the transport.
- No real network calls in tests — every test patches the underlying SDK module.

## Context & Research

### Relevant Code and Patterns

- `src/qbr/llm.py` — interface, both providers, `TokenUsage`, `UsageTracker`, `create_client`. (Now also hosts `ClaudeCLIClient` from #49 and `FallbackClient` from #28; both extend the same `LLMClient` ABC.)
- `src/qbr/models.py` — provides the Pydantic types but the LLM module owns its own `TokenUsage`.
- `src/qbr/cli.py` — `smoke-test` command exercises the factory.
- `tests/test_llm.py` — 14 tests across `TestTokenUsage`, `TestUsageTracker`, `TestAnthropicClient`, `TestOllamaClient`, `TestCreateClient`.

## Key Technical Decisions

- **Single `complete()` method, return `str | dict`.** Rationale: callers know whether they passed a schema; branching on return type is cleaner than two separate methods (`complete_text` vs `complete_structured`) when the underlying providers genuinely have different code paths.
- **Anthropic structured output via tool_use, not raw JSON-mode.** Rationale: at the time this shipped, tool_use with a single forced tool was the most reliable way to constrain Claude's JSON output. The schema is passed as the tool's `input_schema`; Anthropic's tool-call API guarantees the model produces JSON conforming to that schema. The extraction pipeline can therefore parse without `try/except json.JSONDecodeError` defensiveness.
- **Ephemeral cache on the system prompt.** Rationale: per-thread extraction calls reuse the same system prompt + Colleagues roster. Wrapping that block with `cache_control: {"type": "ephemeral"}` cuts the input cost to ~10% on cache hits, which is the largest single lever for keeping the demo run cheap. The client always sets `cache_control` on the system block when one is provided — there is no opt-out, since hits are free and misses are no worse than uncached.
- **Token usage as a Pydantic model, accumulated by a separate `UsageTracker`.** Rationale: keeps each call self-describing (`TokenUsage(input_tokens=…, output_tokens=…, cache_read=…, cache_creation=…, model=…)`) while letting the pipeline aggregate across many calls without each client knowing about the run-level total. The web Processing Log and the CLI verbose output both consume `UsageTracker.summary()`.
- **Cost estimation lives on `TokenUsage`** with a hard-coded price table for Haiku 4.5 and Sonnet 4.6. Rationale: prices change rarely; centralising them in one model means one place to update when they do, and means tests can assert exact dollar amounts without mocking a pricing service.
- **Retry with exponential backoff is internal to the Anthropic client.** Rationale: rate-limit / 5xx handling is a transport concern; surfacing it to the pipeline would require every caller to repeat the same `try/except`. Backoff is bounded so a permanently-failing key still surfaces.
- **`create_client(provider, …)` factory keyed by string.** Rationale: matches the `QBR_LLM_PROVIDER` env var directly, lets callers thread one shared `UsageTracker` through (`test_shared_tracker` proves this works), and lets later providers (claude-cli, fallback) slot in without touching call sites.
- **Mocked tests only.** Rationale: real-API tests would need credentials in CI and would burn quota on every push. Patching `anthropic` and `ollama` modules at import time gives full coverage of the request/response shape without that.

## Implementation Units

- [x] **Unit 1: Token usage accounting + cost estimation**

  **Goal:** A self-describing per-call usage record with cost math, plus an accumulator the pipeline can pass through every client call.

  **Files:**
  - `src/qbr/llm.py`
  - `tests/test_llm.py`

  **Approach:**
  - `TokenUsage(BaseModel)` carries `input_tokens`, `output_tokens`, `cache_read`, `cache_creation`, `model`. Methods compute total tokens and cost using the Haiku 4.5 / Sonnet 4.6 price table from the issue body.
  - `UsageTracker` keeps a `list[TokenUsage]` and exposes `record(...)` and a `summary()` view used by the CLI/web logs.

  **Test scenarios:**
  - `tests/test_llm.py::TestTokenUsage::test_total_tokens / test_cost_haiku / test_cost_with_cache`
  - `tests/test_llm.py::TestUsageTracker::test_empty / test_record_and_summary`

- [x] **Unit 2: Anthropic client with caching, structured output, retries**

  **Goal:** `AnthropicClient.complete(...)` that handles text and schema-bound calls, applies `cache_control` to the system block, retries with backoff, and records usage on the shared tracker.

  **Files:**
  - `src/qbr/llm.py`
  - `tests/test_llm.py`

  **Approach:**
  - Wraps the `anthropic` SDK client. When `response_schema` is `None`, posts a normal messages call and returns the assistant text. When provided, posts the schema as a single forced tool's `input_schema` and returns the tool-input dict — the API guarantees schema-valid JSON.
  - On every call, wraps the system message in a block carrying `cache_control: {"type": "ephemeral"}` so the same prefix is cache-hit on subsequent extraction calls.
  - On `RateLimitError` / 5xx, retries with exponential backoff, bounded.
  - After each successful response, builds a `TokenUsage` from the API's `usage` field (input/output/cache_read/cache_creation) and records it on the injected `UsageTracker`.

  **Test scenarios:**
  - `tests/test_llm.py::TestAnthropicClient::test_complete_text` — text path returns the assistant message, records usage.
  - `tests/test_llm.py::TestAnthropicClient::test_complete_structured` — schema-bound call returns the tool input dict.
  - `tests/test_llm.py::TestAnthropicClient::test_cache_system_prompt` — asserts the system block is sent with `cache_control` when a system prompt is provided.

- [x] **Unit 3: Ollama client (parallel implementation for offline dev)**

  **Goal:** A drop-in `OllamaClient` so the pipeline runs locally without API keys, satisfying the "fallback/dev" half of the issue.

  **Files:**
  - `src/qbr/llm.py`
  - `tests/test_llm.py`

  **Approach:**
  - Wraps `ollama.Client`. Text path returns `response['message']['content']`. Schema-bound path passes `format='json'` (Ollama's JSON mode) and `json.loads`-es the response. No tool_use equivalent on Ollama; JSON mode is best-effort.
  - Records `TokenUsage` from `prompt_eval_count` / `eval_count` (the Ollama field names), `cache_read=0` because Ollama has no equivalent.

  **Test scenarios:**
  - `tests/test_llm.py::TestOllamaClient::test_complete_text`
  - `tests/test_llm.py::TestOllamaClient::test_complete_structured`

- [x] **Unit 4: Factory + shared tracker**

  **Goal:** `create_client(provider, *, tracker=...)` so the pipeline can switch providers via `QBR_LLM_PROVIDER` and thread one shared `UsageTracker` through every call.

  **Files:**
  - `src/qbr/llm.py`
  - `src/qbr/cli.py` (consumer)
  - `tests/test_llm.py`

  **Approach:**
  - `create_client` dispatches on the provider string (`"anthropic"`, `"ollama"`) and returns the matching client, passing through the shared `UsageTracker` and any model-selection kwargs.
  - Unknown providers raise — silent fallback would hide misconfiguration.
  - Wired into the `qbr smoke-test` command so the user can prove a provider responds end-to-end.

  **Test scenarios:**
  - `tests/test_llm.py::TestCreateClient::test_create_anthropic`
  - `tests/test_llm.py::TestCreateClient::test_create_ollama`
  - `tests/test_llm.py::TestCreateClient::test_unknown_provider`
  - `tests/test_llm.py::TestCreateClient::test_shared_tracker` — the same tracker instance accumulates across two clients constructed by the factory.

## Lineage / Follow-ups

- **`FallbackClient`** (PR #28 / #49 era) wraps a primary client and falls back to a local Ollama on failure. Lives in the same `src/qbr/llm.py` and reuses this issue's `LLMClient` interface unchanged.
- **`ClaudeCLIClient`** (PR #49, commit `2b96a41`) adds a Claude Code OAuth-subscription provider exposed via the local CLI. Same `LLMClient` ABC; no caching support (the CLI doesn't expose `cache_control`).
- **`create_hybrid_clients`** helper now wraps each primary client in a `FallbackClient` so a transient Anthropic/CLI error degrades gracefully to Ollama.

These follow-ups validate the abstraction: each new provider was a new subclass + a factory branch, with no caller changes.

## Sources & References

- Issue: <https://github.com/peterkolcza/attrecto-qbr-task/issues/3>
- PR: <https://github.com/peterkolcza/attrecto-qbr-task/pull/17>
