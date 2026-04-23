---
title: "feat: Docker + Caddy deployment bundle with auto-HTTPS"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #12"
shipped_in: "PR #25 (commit fdbb390); follow-ups PR #48 (commit 77ae422), PR #51 (commit 3b241d2)"
---

# feat: Docker + Caddy deployment bundle with auto-HTTPS

## Overview

Containerise the FastAPI web app from #11 and put it behind Caddy as a reverse proxy with automatic Let's Encrypt HTTPS. Two services in one `docker-compose.yml`: `web` (multi-stage Dockerfile, `python:3.12-slim`, uv-installed, non-root user) and `caddy` (`caddy:2-alpine`, terminates TLS on 80/443). A `.env.prod.example` template documents the runtime knobs; an `.env` file is mounted into the web container at runtime so secrets stay out of the image.

The bundle was followed by two corrective commits: PR #48 (`77ae422`) restored a missing `README.md` copy that broke the builder stage's final `uv sync`, and PR #51 (`3b241d2`) installed Node.js 20 + the Claude Code CLI so the `claude-cli` LLM provider works inside the container — including the non-trivial dance of giving the `qbr` user a real shell, a writable `$HOME`, and a pre-created `~/.claude/` directory.

## Problem Frame

Once #11 produced a runnable `uvicorn qbr_web.app:app`, the next concrete step was getting it onto a public URL the evaluator could open. The constraints: a single small Oracle VPS, no managed cloud, Ollama running on the host (not in the container — limited GPU/CPU sharing), and HTTPS without manual certbot wrangling. Caddy in front of FastAPI hits all three: zero-config Let's Encrypt, automatic certificate renewal, and clean reverse-proxy semantics so the app process stays HTTP-only.

## Requirements Trace

- R1. **DONE** — Multi-stage `Dockerfile` based on `python:3.12-slim`, uv-based install, non-root `qbr` user.
- R2. **DONE** — `docker-compose.yml` defines `web` + `caddy` services with `restart: unless-stopped` and a named `reports` volume.
- R3. **DONE** — `Caddyfile` reverse-proxies to `web:8000` with `{$QBR_DOMAIN:localhost}` so the same file works locally and in production; auto-HTTPS via Let's Encrypt is the Caddy default.
- R4. **DONE** — `.env.prod.example` documents `QBR_DOMAIN`, `QBR_LLM_PROVIDER`, `ANTHROPIC_API_KEY` (commented), `OLLAMA_HOST` (defaults to `http://host.docker.internal:11434` so the container reaches Ollama on the VPS), and `OLLAMA_MODEL`.
- R5. **DONE** — `/healthz` healthcheck wired both at the Dockerfile layer (`HEALTHCHECK CMD python -c ...`) and the compose layer (`healthcheck` block + `caddy depends_on web condition: service_healthy`).
- R6. **DONE (in follow-up #48)** — `README.md` is COPY'd into the builder so `uv sync` doesn't fail on `[project.readme]`.
- R7. **DONE (in follow-up #51)** — Node.js + `@anthropic-ai/claude-code` installed in the runtime image, with shell + HOME fixes so the CLI runs as the non-root user.

## Scope Boundaries

- **No Ollama in the container.** Documented in the env example: Ollama runs on the host, the web container reaches it via `host.docker.internal:11434`. Bundling Ollama would balloon the image and force GPU passthrough.
- **No Caddy basicauth out of the box.** A commented snippet ships in the `Caddyfile` showing how to enable it; the actual switch lives with the operator who deploys.
- **No Caddy admin API exposure.** Only 80/443 are mapped to the host.
- **No CI build/push.** The runbook builds on the VPS (`docker compose up -d --build`); a registry push is a future concern.

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/app.py` `/healthz` (issue #11) — the contract Docker + compose poll.
- `pyproject.toml` `[project.optional-dependencies].web` — what `uv sync --no-dev --extra web` installs.
- `pyproject.toml` `[project].readme = "README.md"` — the field that bites if README is missing during `uv sync` of the project itself (root cause of the #48 fix).
- `src/qbr/llm.py` Claude CLI provider — added later in #49 and depended on by #51's Dockerfile changes.
- `task/sample_data/` — the bundled demo emails, COPY'd into the image so the demo button works without uploads.

## Key Technical Decisions

- **Multi-stage build with uv in the builder.** Rationale: `ghcr.io/astral-sh/uv:latest` is fast and the runtime image stays slim because only the resolved `.venv/` and source carry forward. `uv sync --no-install-project` is run *before* code is copied so dependency installs cache between code-only changes; a second `uv sync` at the end installs the project itself (and that's the step that needs `README.md`, per #48).
- **Non-root `qbr` user with a real shell.** Initial PR used `/sbin/nologin`; #51 switched to `/bin/bash` because the `claude` CLI spawns a shell during boot. `HOME=/app` is set in the env so `~/.claude/` resolves to a writable path inside the container, and `/app/.claude` is pre-created and `chown`'d so the CLI can drop session files even on the first boot of a fresh container.
- **Caddy as the TLS terminator.** Rationale: zero-config Let's Encrypt + automatic renewal + HTTP→HTTPS redirect for free. Caddy v2 reads `{$QBR_DOMAIN:localhost}` from the environment, so the same `Caddyfile` works for local `https://localhost` (self-signed) and production (`qbr.example.com`).
- **Security headers + CSP in the Caddyfile, not the app.** Rationale: keeping HSTS, X-Frame-Options, Referrer-Policy, and the CSP at the proxy layer means the app code stays unaware of deployment surface. CSP allows `https://cdn.tailwindcss.com` and `https://unpkg.com` because the templates pull Tailwind + HTMX from CDNs (per #11's "no build step" decision).
- **Healthcheck at two layers.** The Dockerfile `HEALTHCHECK` makes `docker ps` honest about container readiness; the compose `healthcheck` is what `caddy depends_on web condition: service_healthy` watches so Caddy doesn't start trying to proxy before `/healthz` answers. Both invoke `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"` to avoid adding `curl` to the runtime image (it's installed in #51 only to fetch the NodeSource setup script and is then `apt-get purge`'d).
- **Reports persisted via a named volume.** `reports:/app/reports` survives container rebuilds; the operator can `docker compose cp web:/app/reports/ ./backup/` per the runbook.
- **Token / API key via `env_file`, never baked into the image.** Both `ANTHROPIC_API_KEY` and (later) `CLAUDE_CODE_OAUTH_TOKEN` are injected at runtime from the on-disk `.env` so the image is safe to push.

## Implementation Units

- [x] **Unit 1: Multi-stage Dockerfile**
  - **Goal:** Produce a slim runtime image that runs `uvicorn qbr_web.app:app` as a non-root user with the `web` extras installed.
  - **Files:** `Dockerfile`.
  - **Approach:** Builder stage installs uv, copies `pyproject.toml` + `README.md` (#48), runs `uv sync --no-dev --extra web --no-install-project` to cache deps, then COPYs `src/`, `prompts/`, `task/sample_data/` and runs a final `uv sync --no-dev --extra web` to install the project. Runtime stage creates the `qbr` group/user with `/bin/bash` + `HOME=/app`, sets `PATH=/app/.venv/bin:$PATH` + `PYTHONPATH=/app/src`, COPY's `.venv/` + `src/` + `prompts/` + `task/` + `pyproject.toml` from the builder, creates `/app/reports` and `/app/.claude` (#51) with `chown -R qbr:qbr`, then drops to `USER qbr`. `EXPOSE 8000`. `HEALTHCHECK` polls `/healthz`. `CMD ["uvicorn", "qbr_web.app:app", "--host", "0.0.0.0", "--port", "8000"]`.
  - **Verification:** `docker build .` succeeds; `docker run -p 8000:8000 ...` answers `GET /healthz` with `{"status":"ok"}`.

- [x] **Unit 2: docker-compose orchestration**
  - **Goal:** Bring up `web` + `caddy` together with the right dependency ordering and persistent volumes.
  - **Files:** `docker-compose.yml`.
  - **Approach:** `web` builds from `.`, mounts `.env` via `env_file`, exposes 8000 internally only (no host port mapping — Caddy is the only public entry), mounts the `reports` named volume, and declares its own healthcheck (same probe as the Dockerfile). `caddy` uses `caddy:2-alpine`, mounts `./Caddyfile:/etc/caddy/Caddyfile:ro` plus `caddy_data` + `caddy_config` named volumes (Let's Encrypt cert cache lives in `caddy_data`), publishes 80 + 443 to the host, takes `QBR_DOMAIN` from the host env, and `depends_on: web condition: service_healthy` so it never proxies to a half-booted app.
  - **Verification:** `docker compose up -d --build` brings both services up; `docker compose ps` shows `web` healthy and `caddy` running; Caddy logs report cert acquisition or fall-back to internal cert for localhost.

- [x] **Unit 3: Caddyfile + .env.prod.example**
  - **Goal:** Reverse-proxy config that auto-HTTPS in production, self-signs locally, and ships sensible security defaults.
  - **Files:** `Caddyfile`, `.env.prod.example`.
  - **Approach:** Caddy site block keyed on `{$QBR_DOMAIN:localhost}` so the same file serves both environments. `reverse_proxy web:8000` to the compose service. `encode gzip`. `header` block sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Strict-Transport-Security: max-age=31536000; includeSubDomains`, and a CSP that allows the Tailwind + HTMX CDNs. Commented `basicauth` snippet documents how to lock down the public URL. `log` writes to stdout so `docker compose logs caddy` is the operator's window into TLS health. `.env.prod.example` documents `QBR_DOMAIN`, the LLM provider switch, `ANTHROPIC_API_KEY` (commented), `OLLAMA_HOST=http://host.docker.internal:11434`, and `OLLAMA_MODEL=gemma4:e2b` (the model that fits in 24 GB RAM).
  - **Verification:** Local `docker compose up` + `curl -k https://localhost/healthz` works on a self-signed cert; production deploy obtains a real Let's Encrypt cert per the Caddy logs.

- [x] **Unit 4: Follow-up — README copy + Claude CLI install**
  - **Goal:** Two correctness fixes that landed after the original PR.
  - **Files:** `Dockerfile` (touched twice — by #48 and #51).
  - **Approach:**
    - **#48 (`77ae422`)** — added `README.md` to the early COPY alongside `pyproject.toml` because hatchling reads `[project.readme]` during the second `uv sync` ("install the project itself") at the end of the builder stage. Without it the build dies with `Readme file does not exist: README.md`. The COPY is annotated with a comment so a future "minimization" pass doesn't strip it back out.
    - **#51 (`3b241d2`)** — added a single `RUN apt-get update && ... nodejs ... npm install -g @anthropic-ai/claude-code && apt-get purge -y curl && apt-get clean && rm -rf /var/lib/apt/lists/*` block so Node 20 + the Claude Code CLI ship with the runtime image. Switched the `qbr` user shell from `/sbin/nologin` to `/bin/bash`, set `ENV HOME="/app"`, and added `/app/.claude` to the `mkdir -p` line so the CLI can write its session files as the non-root user. The CLI authenticates via `CLAUDE_CODE_OAUTH_TOKEN` injected by the compose `env_file`, so no API key is baked in.
  - **Verification:** Build succeeds end-to-end; `docker exec ... claude --version` reports the installed CLI; a demo run with `QBR_LLM_PROVIDER=claude-cli` completes inside the container.

## Sources & References

- GitHub issue: #12 — "Docker + Caddy deployment bundle"
- Shipping commits:
  - `fdbb390` (PR #25) — original bundle
  - `77ae422` (PR #48) — README copy fix
  - `3b241d2` (PR #51) — Claude Code CLI install + non-root runtime fixes
- Files:
  - `Dockerfile`
  - `docker-compose.yml`
  - `Caddyfile`
  - `.env.prod.example`
- Upstream: #11 (web app + `/healthz`), #49 (Claude CLI provider) — #51 makes it actually usable in the container
- Downstream: #13 (Oracle VPS runbook + smoke test consume this bundle)
