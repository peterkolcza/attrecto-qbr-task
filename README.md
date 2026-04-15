# QBR Portfolio Health Report — AI-Driven Email Analysis

Automated system that analyzes project email communications and generates a **Portfolio Health Report** for a Director of Engineering's Quarterly Business Review (QBR). Surfaces unresolved action items, emerging risks, and blockers across multiple projects — with full source attribution.

## Quick Start

```bash
# Prerequisites: Python ≥ 3.12, uv (https://docs.astral.sh/uv/)

# Install
git clone https://github.com/peterkolcza/attrecto-qbr-task.git
cd attrecto-qbr-task
make install

# Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY or QBR_LLM_PROVIDER=ollama

# Run on sample data
make run

# Run with full debug output (prompts, responses, tokens)
make run-debug
```

## Usage

```bash
# Full pipeline: parse → extract → classify → report
qbr run --input task/sample_data --output reports/

# With Ollama (local, no API key needed)
qbr run --provider ollama

# Debug mode: shows all prompts, LLM responses, token usage
qbr run --debug

# Smoke test: verify LLM provider connection
qbr smoke-test --provider anthropic
```

### Example Output

```
╭──── QBR ────╮
│ QBR Portfolio Health Analyzer v0.1.0         │
│ Provider:    Anthropic (Haiku 4.5 → Sonnet)  │
│ Pipeline:    3-stage extraction + 2 Flags     │
│ Security:    Spotlighting + dual-LLM          │
╰──────────────────────────────────────────────╯

✓ Parsed 18 threads across 3 projects
✓ Extracted 47 items, 23 open
      → Project Phoenix: 15 items (8 open)
      → Project Omicron: 18 items (9 open)
      → DivatKirály: 14 items (6 open)
✓ 7 flags triggered
      → Flag 1 (Unresolved Actions): 4 items
      → Flag 2 (Risks/Blockers): 3 items
✓ Report saved:
      → Markdown: reports/portfolio_20250630_120000.md
      → JSON: reports/portfolio_20250630_120000.json
```

## Architecture

```
Email files → Parser → [Haiku] Extraction → [Haiku] Resolution → [Python] Aging → Flags → [Sonnet] Report
                         ↑ QUARANTINE ZONE ↑                                        ↑ PRIVILEGED ZONE ↑
```

**3-stage pipeline per thread:**
1. **Extraction** (Haiku 4.5): quote-first-then-analyze — finds commitments, questions, risks, blockers
2. **Resolution tracking** (Haiku 4.5): determines if each item was resolved within the thread
3. **Aging & severity** (deterministic Python): computes days open, role-based severity scoring

**2 Attention Flags:**
- **Unresolved High-Priority Action Items** — things that fell through the cracks
- **Emerging Risks / Blockers** — problems without a resolution path

See [`Blueprint.md`](Blueprint.md) for the full architectural design, prompt texts, and trade-off analysis.

## AI Model Choices & Justification

### Why Anthropic Claude

1. **Instruction-following quality**: Claude excels at structured extraction from messy real-world text — critical for parsing multi-threaded email conversations with mixed languages and inconsistent formatting.

2. **Prompt caching**: Anthropic's ephemeral caching (`cache_control`) reduces input costs by ~90% when the system prompt is reused across email threads. This is a concrete cost advantage over providers that don't offer prompt-level caching.

3. **Structured outputs**: Claude's tool-use based structured output guarantees schema-valid JSON, eliminating parse errors that plague free-text JSON generation.

4. **Tiered model lineup**: Haiku 4.5 for cheap extraction ($1/M input) + Sonnet 4.6 for high-quality synthesis ($3/M input) — a natural cost/quality split that maps directly to our pipeline stages.

### Why Haiku for Extraction, Sonnet for Synthesis

- **Extraction** (find quotes, classify items) is a structured, low-reasoning task. Haiku 4.5 matches Sonnet 4's extraction accuracy at 1/3 the cost.
- **Synthesis** (cross-project patterns, executive summary) needs nuanced reasoning and writing quality. Sonnet 4.6 is justified for this single high-value call per run.

### Ollama as Fallback

The system supports Ollama (e.g., `llama3.1:8b`) as a local fallback for:
- **Development**: no API key needed, instant iteration
- **Offline operation**: the system works without internet access
- **Cost management**: zero-cost option for testing and non-critical runs
- **Vendor lock-in mitigation**: proves the architecture isn't Anthropic-specific

Quality is lower with local models, but the pipeline architecture is identical.

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
└── models.py       # Pydantic data models

prompts/            # Versioned LLM prompt files
tests/              # 119 tests (parser, LLM, pipeline, flags, security, report)
task/sample_data/   # 18 sample email threads + Colleagues.txt
```

## Development

```bash
make install    # Install all dependencies
make test       # Run test suite (119 tests)
make lint       # Ruff lint check
make format     # Auto-fix lint + format
```

## Deliverables

- [x] [`Blueprint.md`](Blueprint.md) — architecture, design decisions, trade-offs
- [x] [`README.md`](README.md) — setup, usage, model justification (this file)
- [x] Working PoC: 119 tests, full pipeline CLI

## Source Material

- `task/AI_Developer.pdf` — original task specification
- `task/sample_data/` — 18 sample project emails + `Colleagues.txt`
