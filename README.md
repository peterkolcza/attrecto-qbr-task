# QBR Portfolio Health Report — AI-Driven Email Analysis

Automated system that analyzes project email communications and generates a **Portfolio Health Report** for a Director of Engineering's Quarterly Business Review (QBR). Surfaces unresolved action items, emerging risks, and blockers across multiple projects — with full source attribution.

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Python** | ≥ 3.12 | `python3 --version` |
| **uv** | any | [Install uv](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Ollama** | any | [Install Ollama](https://ollama.com/download) — required for the default local LLM provider |
| **gemma4 model** | e2b (2B) or larger | `ollama pull gemma4:e2b` — fits in ~4GB RAM. For better quality: `ollama pull gemma4:26b` (needs ~16GB) |

**Optional:** Anthropic API key if you want to use Claude models instead of Ollama.

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/peterkolcza/attrecto-qbr-task.git
cd attrecto-qbr-task

# 2. Install Python dependencies
make install
# This runs: uv sync --all-extras

# 3. Pull the default LLM model
ollama pull gemma4:e2b

# 4. Configure environment
cp .env.example .env
# Default config uses Ollama + gemma4:e2b — no API key needed
# Edit .env to change model or switch to Anthropic
```

### Verify installation

```bash
# Run test suite
make test

# Verify LLM connection
uv run qbr smoke-test
```

## Usage

### CLI — Generate a Portfolio Health Report

```bash
# Run the full pipeline on the 18 sample emails
uv run qbr run

# Shorthand via Makefile
make run

# With debug output (shows all prompts, LLM responses, token counts)
make run-debug
```

The CLI processes emails through a 4-step pipeline and shows real-time progress:

```
╭──────────────────────── QBR ────────────────────────╮
│ QBR Portfolio Health Analyzer v0.1.0                │
│ ────────────────────────────────────────            │
│ Provider:    Ollama (Local model)                   │
│ Pipeline:    3-stage extraction + 2 Attention Flags │
│ Security:    Spotlighting + dual-LLM quarantine     │
│ Caching:     N/A                                    │
│ Debug:       OFF                                    │
╰─────────────────────────────────────────────────────╯

✓ Parsed 18 threads across 3 projects
✓ Extracted 78 items, 19 open
      → Project Phoenix: 27 items (8 open)
      → Project Omicron: 28 items (8 open)
      → DivatKirály: 23 items (3 open)
✓ 16 flags triggered
      → Flag 1 (Unresolved Actions): 14 items
      → Flag 2 (Risks/Blockers): 2 items
✓ Report saved:
      → Markdown: reports/portfolio_20260415_222223.md
      → JSON: reports/portfolio_20260415_222223.json

     Token Usage Summary
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Metric          ┃   Value ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Total LLM calls │      37 │
│ Input tokens    │  45,032 │
│ Output tokens   │  22,728 │
│ Estimated cost  │ $0.4760 │
└─────────────────┴─────────┘
```

### CLI Options

```bash
uv run qbr run --help

Options:
  --input TEXT      Path to email directory [default: task/sample_data]
  --output TEXT     Output directory for reports [default: reports/]
  --provider TEXT   LLM provider: anthropic or ollama [default: from .env]
  --debug           Enable debug mode with full prompt/response traces
```

### Other commands

```bash
uv run qbr smoke-test          # Verify LLM provider connection
uv run qbr seed-demo           # Show pre-loaded project data
```

### Web UI

```bash
make web
# Opens at http://localhost:8000
```

The web dashboard shows:
- **Portfolio overview**: 3 pre-loaded projects with team rosters and known risks
- **"Process Demo Emails" button**: runs the full pipeline with real-time SSE progress
- **Report view**: rendered Markdown + flag sidebar with severity indicators

## Configuration

Edit `.env` to change settings:

```bash
# Provider: "ollama" (default, local) or "anthropic" (cloud)
QBR_LLM_PROVIDER=ollama

# Ollama settings
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:e2b        # 2B model, fast, fits anywhere
# OLLAMA_MODEL=gemma4:26b      # 26B model, better quality, needs 16GB+ RAM

# Anthropic (if using Claude)
# ANTHROPIC_API_KEY=sk-ant-api03-...
```

### Recommended models by RAM

| RAM | Ollama Model | Quality | Speed |
|-----|-------------|---------|-------|
| 4 GB | `gemma4:e2b` | Good | Fast |
| 8 GB | `gemma4:e4b` | Better | Fast |
| 16 GB | `gemma4:26b` | Very good | Medium |
| 24 GB+ | `gemma4:31b` | Excellent | Slower |
| Cloud | Claude Haiku 4.5 + Sonnet 4.6 | Best | Fast |

## Architecture

```
Email files → Parser → [LLM] Extraction → [LLM] Resolution → [Python] Aging → Flags → [LLM] Report
                        ↑ QUARANTINE ZONE ↑                                     ↑ PRIVILEGED ZONE ↑
```

**3-stage pipeline per thread:**
1. **Extraction**: quote-first-then-analyze — finds commitments, questions, risks, blockers
2. **Resolution tracking**: determines if each item was resolved within the thread
3. **Aging & severity** (deterministic Python): computes days open, role-based severity scoring

**2 Attention Flags:**
- **Unresolved High-Priority Action Items** — things that fell through the cracks
- **Emerging Risks / Blockers** — problems without a resolution path

**Security:** 3-layer defense — spotlighting delimiters, input sanitization, output grounding (fuzzy quote matching)

See [`Blueprint.md`](Blueprint.md) for the full architectural design, prompt texts, and trade-off analysis.

## AI Model Choices & Justification

### Default: Ollama + Gemma 4

The default configuration uses **Ollama with Google's Gemma 4** model family. This was chosen because:
- **Zero cost**: runs locally, no API key or cloud subscription needed
- **Privacy**: email data never leaves your machine
- **Good quality**: Gemma 4 (even the 2B variant) handles structured extraction well
- **Scalable**: on a 24GB Oracle VPS, `gemma4:26b` provides near-cloud quality

### Alternative: Anthropic Claude

For production/cloud use, the system supports **Claude Haiku 4.5** (extraction) and **Claude Sonnet 4.6** (synthesis):
- **Best quality**: Claude excels at structured extraction from messy multilingual text
- **Prompt caching**: ~90% input cost reduction when system prompt is reused
- **Structured outputs**: guaranteed schema-valid JSON via tool-use
- **Cost**: ~$0.40 per run on 18 emails (with Haiku/Sonnet tier split)

### Provider-agnostic design

The `LLMClient` abstraction means any provider works with the same pipeline. Switching is one env var change (`QBR_LLM_PROVIDER`).

## Project Structure

```
src/qbr/
├── cli.py          # Typer CLI with verbose/debug output
├── parser.py       # Email parsing, thread grouping, project attribution
├── llm.py          # LLM client abstraction (Anthropic + Ollama)
├── pipeline.py     # 3-stage extraction pipeline
├── flags.py        # Attention Flag classification
├── security.py     # Prompt injection defense + output grounding
├── report.py       # Portfolio Health Report generator
├── models.py       # Pydantic data models
└── seed.py         # Demo project seed data

src/qbr_web/       # FastAPI + HTMX web UI
prompts/            # Versioned LLM prompt files
tests/              # full test suite (parser, pipeline, flags, web, auth, security)
task/sample_data/   # 18 sample email threads + Colleagues.txt
deploy/             # Oracle VPS deployment runbook + smoke test
```

## Development

```bash
make install    # Install all dependencies
make test       # Run full test suite
make lint       # Ruff lint check
make format     # Auto-fix lint + format
make web        # Start web UI dev server
```

## Deployment (Oracle VPS)

See [`deploy/README.md`](deploy/README.md) for the full step-by-step guide. Quick version:

```bash
# On the VPS:
git clone https://github.com/peterkolcza/attrecto-qbr-task.git
cd attrecto-qbr-task
cp .env.prod.example .env
# Edit .env: set QBR_DOMAIN, optionally ANTHROPIC_API_KEY
docker compose up -d --build
```

## Deliverables

- [x] [`Blueprint.md`](Blueprint.md) — architecture, design decisions, trade-offs (5 sections)
- [x] [`README.md`](README.md) — setup, usage, model justification (this file)
- [x] Working PoC: comprehensive test suite, CLI + web UI, Docker deployment

## Source Material

- `task/AI_Developer.pdf` — original task specification
- `task/sample_data/` — 18 sample project emails + `Colleagues.txt`
