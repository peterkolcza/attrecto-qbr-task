---
title: "docs: Blueprint.md — architecture & design document covering all 5 graded sections"
type: docs
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #9"
shipped_in: "PR #22 (commit 8dadbf3); expanded by PR #34 (commit ff9005c) and PR #43 (commit 8b2c4cf); refreshed by PR #52 (commit e99247a)"
---

# docs: Blueprint.md — architecture & design document covering all 5 graded sections

## Overview

`Blueprint.md` is the primary graded deliverable for the Attrecto QBR task. It documents the full system design — how raw project emails become a Portfolio Health Report — at a level a Director of Engineering can review and a senior engineer can implement against. Quality of thinking is weighted higher than code volume by the brief, so this doc is the centre of gravity, not the PoC code.

The initial commit (`8dadbf3`, PR #22) shipped all five required sections in 293 lines. Two follow-ups expanded the doc: PR #34 added a dedicated Security Considerations subsection (LLM01/LLM02/upload), and PR #43 fleshed out the Scale-Out Architecture with parallel workers, autoscaling, queues, and a PoC→Production storage migration table. PR #52 brought the document in sync with the `claude-cli` provider and `FallbackClient` behaviours that landed in PRs #46/#47/#49/#51.

## Problem Frame

The task PDF (`task/AI_Developer.pdf`) lists five sections that must appear in the Blueprint:
1. Data ingestion & scaling approach.
2. The multi-step analytical engine, with 1–2 Attention Flags defined and justified.
3. Engineered prompts, presented inline.
4. Robustness + cost management.
5. Monitoring/trust + concluding statement of the single biggest architectural risk and its mitigation.

Issue #9 lifts those five into acceptance criteria and adds requirements for diagrams, a quote-first-then-analyze rationale, and a dual-LLM-quarantine explanation for the security story. Without this document, the rest of the deliverable (PoC + README) has no design context the evaluators can grade against.

## Requirements Trace

- R1. DONE — Section 1 "Data Ingestion & Initial Processing" (`Blueprint.md:7`) covers the regex parser (stdlib `email` won't accept this format), dual date format handling, diacritic normalization, thread grouping via subject prefix + Colleagues roster, social/off-topic filtering, and the scale-out architecture with mermaid diagram and PoC→production storage table.
- R2. DONE — Section 2 "The Analytical Engine" (`Blueprint.md:110`) defines two Attention Flags (`Unresolved Action Items`, `Risks/Blockers`), justifies the choice (immediate-action vs forward-risk), walks through the Extract → Resolve → Score → Classify → Synthesize pipeline, explains the dual-LLM quarantine and quote-first-then-analyze patterns, and lists the hallucination mitigation strategy.
- R3. DONE — Section 2 includes the four engineered prompts inline (Stage A extraction, Stage B resolution, the deterministic Stage C scoring, and the synthesis prompt) with annotation explaining the design choices for each.
- R4. DONE — Section 3 "Cost & Robustness Considerations" (`Blueprint.md:275`) covers the Haiku/Sonnet tiered split with concrete per-million pricing, prompt caching (system + roster), Batch API stacking, the OAuth/`claude-cli` zero-marginal-cost path (added in PR #52), Ollama as a fallback, retry/backoff, and the `FallbackClient` documented from PR #52.
- R5. DONE — Section 4 "Monitoring & Trust" (`Blueprint.md:319`) lists the 8 key metrics, the trust framework, and the regression-testing approach.
- R6. DONE — Section 5 "Architectural Risk & Mitigation" (`Blueprint.md:346`) names the primary risk (no ground-truth for automated flag validation) with a 5-point mitigation, plus a secondary risk (external API dependency) whose mitigation was upgraded in PR #52 from a single Ollama-fallback sentence to a multi-layer provider strategy.
- R7. DONE — Security Considerations subsection (`Blueprint.md:260`, added in PR #34) covers OWASP LLM01 (prompt injection via spotlighting + dual-LLM quarantine), LLM02 (output handling), and upload security.
- R8. DONE — Mermaid ingestion-pipeline diagram present in Section 1.

## Scope Boundaries

- No code listings beyond what already exists in the engineered-prompts subsections — the Blueprint links out to PoC files, it doesn't duplicate them.
- No vendor procurement / pricing negotiation content — cost discussion stays at architecture-decision level (per-million model pricing as published).
- No alternative-architecture compare table (e.g. RAG vs fine-tuning vs current). The brief asks for the chosen design and its rationale, not a survey.
- No operational runbook content — that lives implicitly in the README's deployment section.

## Context & Research

### Relevant Code and Patterns

- `task/AI_Developer.pdf` — authoritative spec; the five required sections are pulled directly from it.
- `task/sample_data/` — the 18 sample emails plus `Colleagues.txt` are the empirical basis for parser-design claims (non-RFC headers, dual date formats, diacritic mismatches between `From:` line and email).
- `src/qbr/parser.py`, `src/qbr/pipeline.py`, `src/qbr/flags.py`, `src/qbr/report.py`, `src/qbr/llm.py` — the implementation the Blueprint describes; prompt files in `src/qbr/prompts/` are quoted inline.
- `src/qbr/security.py` — spotlighting / quarantine implementation referenced from the Security Considerations subsection.

## Key Technical Decisions

- **One document, not a `docs/` tree.** A single Markdown file is what the evaluators are graded against. Multi-file docs sites add navigation cost without adding signal for a 300-line design doc.
- **Prompts inline, not linked.** The brief explicitly asks for "engineered prompts presented inline" — readers shouldn't need to open another file to evaluate prompt quality.
- **Mermaid for the only diagram.** GitHub renders mermaid natively; no PNG to keep in sync with code changes.
- **Cost section uses concrete numbers.** Per-million-token prices for Haiku and Sonnet are stated explicitly so the cost claim ("~95% reduction with caching + Batch API") is auditable.
- **Architectural-risk section names a single primary risk.** The brief says "the single biggest architectural risk" — naming two would dilute the answer. Secondary risk gets one paragraph at the end as a deliberate scope boundary.
- **Follow-up PRs expanded rather than rewrote.** PR #34 (security), PR #43 (scaling), PR #52 (claude-cli + FallbackClient) added subsections without restructuring — the original five-section skeleton was sound.

## Implementation Units

- [x] **Unit 1: Section 1 — Data Ingestion & Initial Processing**

  **Goal:** Document the parser design and the path from PoC (in-memory + files) to a production scale-out architecture.

  **Files:**
  - `Blueprint.md` (`## 1. Data Ingestion & Initial Processing`)

  **Approach:** PoC subsection covers the custom regex parser, dual date format support, diacritic normalization, thread grouping by subject prefix + roster matching, and social-message filtering. Scale-out subsection (expanded in PR #43) gives the email-provider → S3 → SQS → worker-pool architecture, the PoC→production storage migration table, parallel-agent strategy, autoscaling, off-hours batch processing, and resilience patterns (DLQ, circuit breaker, idempotency). One mermaid diagram of the ingestion pipeline.

  **Verification:** Section 1 present with the parser-design subsection, the scale-out subsection, the storage migration table, and a mermaid diagram. Headings visible at `Blueprint.md:7`, `:9`, `:28`, `:47`, `:58`, `:80`, `:90`, `:97`.

- [x] **Unit 2: Section 2 — The Analytical Engine**

  **Goal:** Define and justify the two Attention Flags, document the multi-step pipeline, present the engineered prompts inline, and cover the security considerations.

  **Files:**
  - `Blueprint.md` (`## 2. The Analytical Engine (Multi-Step AI Logic)`)

  **Approach:** Attention Flag definitions with justification (immediate action vs forward risk). Multi-step pipeline narrative (Extract → Resolve → Score → Classify → Synthesize) with the dual-LLM quarantine and quote-first-then-analyze patterns explained. Engineered Prompts subsection includes Stage A (extraction), Stage B (resolution), Stage C (deterministic — no prompt), and Synthesis prompts, each annotated with design rationale. Hallucination Mitigation Strategy covers Pydantic schema validation, quote grounding, and structured outputs. Security Considerations subsection (added in PR #34) covers LLM01/LLM02 + upload security.

  **Verification:** Two Attention Flags defined with justification; four prompt blocks present inline; "Hallucination Mitigation Strategy" and "Security Considerations" subsection headings present. Headings visible at `Blueprint.md:110`, `:112`, `:130`, `:168`–`:251`, `:260`.

- [x] **Unit 3: Section 3 — Cost & Robustness Considerations**

  **Goal:** Justify the Haiku/Sonnet split with concrete prices, document prompt caching + Batch API + OAuth-via-CLI cost paths, and explain the robustness story (FallbackClient, retries, graceful degradation).

  **Files:**
  - `Blueprint.md` (`## 3. Cost & Robustness Considerations`)

  **Approach:** Cost Management table with per-million pricing for Haiku, Sonnet, prompt caching, Batch API, and the `claude-cli` zero-marginal-cost path (added in PR #52 with rationale for when it makes sense — live demo without handing out API keys). Robustness subsection covers retry/backoff, off-topic filtering, ambiguous-status handling, severity thresholds, and `FallbackClient` (documented in PR #52: what triggers it, what gets logged, why the run keeps going).

  **Verification:** "Cost Management" and "Robustness" subsections present with the cost table and Ollama / claude-cli entries; FallbackClient described. Headings at `Blueprint.md:275`, `:277`, `:297`.

- [x] **Unit 4: Section 4 — Monitoring & Trust**

  **Goal:** Enumerate the metrics that prove the system is working and the framework that lets the Director trust the output.

  **Files:**
  - `Blueprint.md` (`## 4. Monitoring & Trust`)

  **Approach:** Key Metrics subsection covers token usage per call/run, per-flag precision/recall against a manually annotated set, drift detection, processing-log transparency, and user-feedback signals. Trust Framework subsection ties those metrics to the dashboard's confirm/dismiss affordance and to the regression test suite.

  **Verification:** "Key Metrics" and "Trust Framework" subsections present with the 8-metric list. Headings at `Blueprint.md:319`, `:321`, `:334`.

- [x] **Unit 5: Section 5 — Architectural Risk & Mitigation**

  **Goal:** Name the single biggest architectural risk and give a concrete, multi-point mitigation. End with a brief secondary-risk paragraph.

  **Files:**
  - `Blueprint.md` (`## 5. Architectural Risk & Mitigation`)

  **Approach:** Primary Risk: "No Ground-Truth for Automated Flag Validation" — a plausible-sounding but wrong flag (e.g., resolved item marked open) directly misleads the Director. Mitigation Strategy: (1) every flag carries auditable evidence (quote + source); (2) human-in-loop confirm/dismiss in the dashboard before flags reach the QBR; (3) regression test suite from manually validated outputs; (4) confidence scoring with "needs review" status; (5) processing-log transparency. Secondary Risk: external API dependency during time-sensitive QBR prep — mitigation upgraded in PR #52 from a single Ollama-fallback sentence to a multi-layer provider strategy (Anthropic API → claude-cli → Ollama via FallbackClient + cached last-known-good reports).

  **Verification:** "Primary Risk" and "Mitigation Strategy" subsections present, plus the "Secondary Risk: External API Dependency" subsection updated with the multi-layer mitigation. Headings at `Blueprint.md:346`, `:348`, `:356`, `:368`.

## Sources & References

- GitHub issue: #9
- Pull requests / commits: PR #22 (commit `8dadbf3`, initial document); PR #34 (commit `ff9005c`, Security Considerations); PR #43 (commit `8b2c4cf`, Scale-Out expansion); PR #52 (commit `e99247a`, claude-cli + FallbackClient + multi-layer secondary-risk mitigation).
- Authoritative spec: `task/AI_Developer.pdf`.
- Implementation referenced by the document: `src/qbr/parser.py`, `src/qbr/pipeline.py`, `src/qbr/flags.py`, `src/qbr/report.py`, `src/qbr/llm.py`, `src/qbr/security.py`, `src/qbr/prompts/{extraction,resolution,synthesis}.md`.
