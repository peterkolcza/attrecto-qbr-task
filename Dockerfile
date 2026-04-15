# Multi-stage Dockerfile for QBR Portfolio Health Report
# Uses uv for fast dependency installation

FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (cache layer)
COPY pyproject.toml ./

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

# Create non-root user
RUN groupadd -r qbr && useradd -r -g qbr -d /app -s /sbin/nologin qbr

# Copy virtual environment and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/prompts /app/prompts
COPY --from=builder /app/task /app/task
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Create reports directory
RUN mkdir -p /app/reports && chown -R qbr:qbr /app

USER qbr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "qbr_web.app:app", "--host", "0.0.0.0", "--port", "8000"]
