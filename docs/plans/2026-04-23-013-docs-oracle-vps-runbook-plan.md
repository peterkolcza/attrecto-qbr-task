---
title: "docs: Oracle VPS deployment runbook + automated smoke test"
type: docs
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #13"
shipped_in: "PR #26 (commit bdd2e87)"
---

# docs: Oracle VPS deployment runbook + automated smoke test

## Overview

Step-by-step operator guide for deploying the QBR web app on a fresh Oracle Cloud VPS, plus a shell smoke-test that verifies the deployment end-to-end. The runbook covers server prep (Docker install), repo clone + `.env` creation, DNS setup, `docker compose up -d --build`, and operational chores (logs, restart, update, backup). The smoke test (`deploy/smoke-test.sh`) probes `/healthz`, the landing page, and a full demo analysis cycle, then confirms the report renders.

No systemd units — Compose's `restart: unless-stopped` policy is sufficient and keeps the deploy footprint to "Docker + a Compose file."

## Problem Frame

#11 produces a runnable web app and #12 ships the container bundle, but a fresh evaluator on a fresh VPS still needs to know which buttons to push in which order — and Oracle's free-tier VPS adds a few specifically-Oracle traps (the security-list firewall in the cloud console *plus* the per-VM `iptables` chain) that aren't obvious from the Docker docs. The smoke test exists so the operator can answer "is it actually working end-to-end?" with a single command instead of clicking through the UI.

## Requirements Trace

- R1. **DONE** — `deploy/README.md` covers DNS, Docker install, clone, `.env` edit, `docker compose up -d --build`.
- R2. **DONE** — `deploy/smoke-test.sh` hits `/healthz`, loads the landing page, starts a demo analysis, polls until complete, and verifies the report endpoint.
- R3. **DONE** — Backup procedure documented (`docker compose cp web:/app/reports/ ./backup-reports/`).
- R4. **DONE** — No systemd units; relies on Compose's `restart: unless-stopped` (declared in `docker-compose.yml`).
- R5. **DONE** — Oracle-specific firewall guidance covers both the Cloud Console security list (ingress on 80/443) and the VM-level `iptables -I INPUT` rules. This is the trap that gets every first-time Oracle deployer.

## Scope Boundaries

- **Operator-grade docs only.** No Terraform, no Ansible, no Cloud-init — the brief is a small VPS where the human SSHs in once.
- **No CI/CD pipeline.** Updates are a manual `git pull && docker compose up -d --build`.
- **Smoke test is bash + curl + python (for JSON parsing), not a pytest target.** Rationale: it runs against a deployed URL, not the local code; pytest would imply an in-process app.
- **No log aggregation, metrics, or alerting.** `docker compose logs -f` is the operator's lens.

## Context & Research

### Relevant Code and Patterns

- `Dockerfile` + `docker-compose.yml` (issue #12) — what the runbook drives.
- `Caddyfile` — `{$QBR_DOMAIN:localhost}` is why the runbook tells the operator to set `QBR_DOMAIN` in `.env` before `up`.
- `.env.prod.example` — the template the runbook tells the operator to `cp` to `.env`.
- `src/qbr_web/app.py` `/healthz`, `POST /analyze`, `GET /jobs/{id}`, `GET /jobs/{id}/report` — the four endpoints the smoke test probes.

## Key Technical Decisions

- **One file, top-to-bottom checklist.** Rationale: deployment runbooks are read once under stress; a single linear narrative beats a per-topic split. Sections are numbered so the operator can resume mid-flow.
- **`get.docker.com` for Docker install.** Rationale: works on Ubuntu ARM and x86 — relevant for Oracle's free-tier Ampere ARM instances. Calls out `usermod -aG docker $USER` + log out/in dance because that's the most common "why doesn't `docker` work without sudo" stumble.
- **Document Oracle's two-layer firewall explicitly.** The Cloud Console security list *and* in-VM `iptables` both block by default on Oracle Linux/Ubuntu images. Documented as side-by-side instructions because forgetting either layer leaves Caddy unable to negotiate Let's Encrypt (cert acquisition fails silently as a TLS handshake timeout).
- **Smoke test exits non-zero on any failure.** Rationale: it's wired to be CI-friendly later. `set -euo pipefail` + per-check pass/fail tally + final `exit 1` if anything failed.
- **Smoke test polls for completion with a 120-second cap (24 × 5 s).** Rationale: Ollama on a CPU-only VPS can be slow; 120 s is enough for a small-model demo run. Beyond that, surface "analysis didn't finish" rather than hanging forever.
- **Smoke test scrapes the rendered job page for state, not the JSON API.** Rationale: at the time of writing, `/api/jobs/{id}/progress` didn't exist yet (it lands later in #45 polling work). Grepping the rendered status pill is a fragile but adequate workaround until the JSON API is generally available; flagged as a known limitation.

## Implementation Units

- [x] **Unit 1: `deploy/README.md` — operator runbook**
  - **Goal:** A linear, numbered guide that takes a fresh Ubuntu VPS to a public HTTPS deployment.
  - **Files:** `deploy/README.md`.
  - **Approach:** Six sections: Prerequisites, Server Setup (Docker install), Clone & Configure (`cp .env.prod.example .env` + edit), DNS Setup (A record), Deploy (`docker compose up -d --build` + log inspection), Verify (curl `/healthz` + run the smoke test), Operations (logs/restart/update/backup), Oracle Cloud Firewall (security list + iptables). Each command is a copy-pasteable code block with comments explaining what to substitute.
  - **Verification:** Document follows the actual paths of `Dockerfile`, `docker-compose.yml`, `.env.prod.example`, and `deploy/smoke-test.sh`; deploying on a new VPS following only this guide produces a working public URL.

- [x] **Unit 2: `deploy/smoke-test.sh` — automated verification**
  - **Goal:** Single-command end-to-end probe an operator can run after deploy or as a release sanity check.
  - **Files:** `deploy/smoke-test.sh`.
  - **Approach:** `set -euo pipefail`. Take `BASE_URL` as `${1:-http://localhost:8000}`. Define a `check "desc" "cmd"` helper that increments PASS/FAIL counters. Five probes: (1) `curl -sf $BASE_URL/healthz | grep -q ok`, (2) `curl -sf $BASE_URL/ | grep -q QBR`, (3) `POST /analyze` and parse `job_id` out of the JSON response with `python3 -c`, (4) `GET /jobs/$JOB_ID` page renders with the job id visible, (5) poll the rendered job page every 5 s up to 120 s, parsing the status pill class to detect `complete` vs `error`, then `GET /jobs/$JOB_ID/report` and grep for "Portfolio". Print a summary line and `exit 1` if any check failed.
  - **Verification:** `bash deploy/smoke-test.sh http://localhost:8000` against a live local container reports all checks passing; against an unreachable URL exits non-zero.

- [x] **Unit 3: Operations + Oracle-specific guidance**
  - **Goal:** Once the deploy is live, give the operator the small set of commands they actually need.
  - **Files:** `deploy/README.md` (sections 6 + "Oracle Cloud Firewall").
  - **Approach:** Operations section documents log inspection (per-service `docker compose logs -f`), restart vs. rebuild semantics (`restart` for config-only, `up -d --build` for code changes), the update flow (`git pull origin main && docker compose up -d --build`), and a backup recipe using `docker compose cp` to extract the `reports/` volume to the host. Oracle-specific section walks through the Console (Networking → VCN → Security Lists → ingress on 80 + 443 from `0.0.0.0/0`) and adds the `sudo iptables -I INPUT -p tcp --dport 80/443 -j ACCEPT` commands to fix the in-VM block.
  - **Verification:** Backup command produces a non-empty `backup-reports/` after a completed analysis; firewall steps unblock Caddy's Let's Encrypt acquisition on a fresh Oracle instance.

## Sources & References

- GitHub issue: #13 — "Oracle VPS deployment runbook + smoke test"
- Shipping commit: `bdd2e87` (PR #26)
- Files:
  - `deploy/README.md`
  - `deploy/smoke-test.sh`
- Upstream: #11 (web app endpoints the smoke test probes), #12 (Docker + Caddy bundle the runbook drives)
