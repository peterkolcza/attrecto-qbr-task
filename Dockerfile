# Multi-stage Dockerfile for QBR Portfolio Health Report
# Uses uv for fast dependency installation

FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (cache layer).
# README.md is referenced by pyproject.toml's [project.readme] and is required
# by hatchling during the "install the project itself" step below.
COPY pyproject.toml README.md ./

# Install dependencies
RUN uv sync --no-dev --extra web --no-install-project

# Copy application code
COPY src/ src/
COPY prompts/ prompts/
COPY task/sample_data/ task/sample_data/

# Install the project itself
RUN uv sync --no-dev --extra web

# --- Production stage ---
FROM python:3.12-slim

WORKDIR /app

# Create non-root user. Need a real shell (/bin/bash) and a writable HOME
# so `claude` CLI (when QBR_LLM_PROVIDER=claude-cli) can boot. Default was
# /sbin/nologin which blocks CLI subprocesses.
RUN groupadd -r qbr && useradd -r -g qbr -d /app -s /bin/bash qbr

# Install Node.js + Claude Code CLI so QBR_LLM_PROVIDER=claude-cli works.
# The CLI authenticates via CLAUDE_CODE_OAUTH_TOKEN env var — no API key,
# no interactive login. Token is injected at runtime via docker-compose
# env_file, never baked into the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy virtual environment and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/prompts /app/prompts
COPY --from=builder /app/task /app/task
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV HOME="/app"

# Create reports directory + claude config dir (claude writes session files there)
RUN mkdir -p /app/reports /app/.claude && chown -R qbr:qbr /app

USER qbr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "qbr_web.app:app", "--host", "0.0.0.0", "--port", "8000"]
