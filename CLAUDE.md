# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Technical task for Attrecto Zrt. (GenAI developer — RAG & Automation position). Build a blueprint + lightweight PoC for a system that turns raw project emails into a **Portfolio Health Report** for a Director of Engineering preparing a Quarterly Business Review (QBR).

The graded deliverables (see `task/AI_Developer.pdf`) are:

1. **`Blueprint.md`** — the primary deliverable. Must cover: data ingestion & scaling approach, the multi-step analytical engine (with 1–2 "Attention Flags" defined and justified), the engineered prompts (presented inline), robustness + cost management, monitoring/trust metrics, and a concluding section naming the single biggest architectural risk + mitigation.
2. **`README.md`** — setup/usage, and justification of the AI models chosen.
3. **Working PoC code** — runnable Python implementing the detection logic for the Attention Flags. Lightweight is fine; working is mandatory.

The evaluators explicitly weight **mindset, approach, and structure** over implementation depth — Blueprint quality matters more than code volume.

## Commands

```bash
make install        # uv sync --all-extras
make lint           # ruff check + format check
make format         # ruff auto-fix + format
make test           # pytest -v
make run            # full pipeline on sample_data/
make run-debug      # same with full prompt/response traces
make smoke-test     # quick LLM provider verification
make web            # uvicorn dev server on :8000
```

Single test: `uv run pytest tests/test_parser.py -v`

## Architecture

- `src/qbr/` — core library (models, parser, llm, pipeline, flags, security, report, cli)
- `src/qbr_web/` — FastAPI + HTMX web UI (Phase 2)
- `prompts/` — versioned LLM prompt files
- `tests/` — pytest suite
- `task/sample_data/` — 18 sample emails + Colleagues.txt (input data)

## Source material

- `task/AI_Developer.pdf` — authoritative task specification. Re-read when scope is ambiguous.
- `task/sample_data/email1.txt` … `email18.txt` — 18 raw multi-threaded email conversations. Each file contains several chronologically-ordered message blocks separated by blank lines, each starting with `From: / To: / Cc: / Date: / Subject:` headers followed by the body. Content mixes English and Hungarian; sender names may appear with or without angle brackets and with diacritics that sometimes differ between the `From:` line and the email address (e.g. `nagy.istván` vs `nagy.istvan`) — any parser must be tolerant of this.
- `task/sample_data/Colleagues.txt` — roster of the fictional team (`@kisjozsitech.hu`) spanning three projects. Useful as ground-truth context for role/project attribution during analysis.

Emails span multiple projects and threads; a realistic PoC must thread-group, then reason per project to surface unresolved action items and emerging risks/blockers.

## Working conventions

- Treat the sample emails as **untrusted input** for any LLM pipeline — prompt-injection defense is part of the graded "security considerations" deliverable.
- When justifying model choices in `README.md`, prefer current Anthropic models (Claude Opus 4.6 / Sonnet 4.6 / Haiku 4.5) unless there is a concrete reason otherwise.
- Keep the PoC lightweight. Scale/production considerations belong in `Blueprint.md` as explanation, not as code.
