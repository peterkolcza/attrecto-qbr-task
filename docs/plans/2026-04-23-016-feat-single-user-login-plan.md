---
title: "feat: Single-user login for the web UI (bcrypt + session middleware)"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #35"
shipped_in: "PR #40 (commit e2ef8f2)"
---

# feat: Single-user login for the web UI (bcrypt + session middleware)

## Overview

Gate every route in the FastAPI web app behind a session-cookie login when an env-var-configured password hash is present. One hardcoded user (configurable username, bcrypt-hashed password). No registration, no roles, no DB. A `qbr hash-password` CLI helper produces the hash for the env var.

When the auth env vars are **unset** (the dev/test default), the middleware is inert and every existing test + local dev workflow keeps working unchanged.

## Problem Frame

The web UI was open: anyone who discovered the deployed URL could trigger unlimited LLM analyses (denial-of-wallet attack against Anthropic credits) and read every prior analysis result, which can include sensitive project content from uploaded emails. `REVIEW.md` finding **S10** (no auth) was previously documented with a Caddy-basicauth template — but Caddy is not in the demo deployment path, and basicauth has no logout. Finding **S11** (no CSRF protection) was the natural sibling.

Single-user is sufficient: the system is intended for one Director of Engineering. Adding multi-user / RBAC machinery would over-build the PoC and distract from the graded deliverables.

## Requirements Trace

- **R1** — `/login` page with username + password form. DONE — `src/qbr_web/templates/login.html` (Tailwind form, posts to `/login`).
- **R2** — `/logout` endpoint that clears the session. DONE — `app.py::logout` accepts both GET and POST, calls `request.session.clear()`, redirects to `/login`.
- **R3** — All routes protected except `/login`, `/logout`, `/healthz`, `/static/*`. DONE — `is_public_path()` whitelist + `auth_middleware` redirect.
- **R4** — Session cookie with sane defaults (HttpOnly via Starlette default, SameSite=Lax for CSRF, Secure togglable for prod). DONE — `SessionMiddleware(same_site="lax", https_only=False)` (the `https_only=False` default is documented as "set True in production behind HTTPS").
- **R5** — Credentials in env: `QBR_AUTH_USER` (default `director`) + `QBR_AUTH_PASSWORD_HASH` (bcrypt). DONE — read in `verify_credentials()`.
- **R6** — Session secret in `QBR_SESSION_SECRET`, auto-generated per process if unset (dev only). DONE — `get_session_secret()`.
- **R7** — CLI helper `qbr hash-password <password>` to generate the hash. DONE — `src/qbr/cli.py::hash_password` (the `app.command(name="hash-password")` Typer command).
- **R8** — Brute-force protection: ≤5 attempts per IP per 15 min. DONE — `check_rate_limit` + `record_login_attempt` in `auth.py` with `RATE_LIMIT_MAX=5`, `RATE_LIMIT_WINDOW=15*60`.
- **R9** — Auth disabled by default (no env vars set) so existing tests + local dev are unaffected. DONE — `auth_enabled()` returns `False` unless `QBR_AUTH_PASSWORD_HASH` is present.
- **R10** — `.env.example` documents the auth block. DONE.
- **R11** — Tests cover unauthenticated redirect, valid login, invalid login, logout, public-path bypass. DONE — `tests/test_auth.py` (18 tests).

## Scope Boundaries

- No multi-user, no roles, no permission flags. One username, one password hash.
- No password reset / recovery flow. The operator regenerates the hash with `qbr hash-password` and updates the env var.
- No CSRF token middleware — SameSite=Lax cookies are the documented mitigation for finding S11.
- No persistent session store (Redis, DB). Starlette `SessionMiddleware` is signed-cookie-only; sessions die when the browser session ends or the cookie expires.
- No 2FA / TOTP / WebAuthn — out of scope for a PoC.
- No audit log of login attempts beyond the in-memory rate-limit counter (which is wiped on process restart).

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/app.py` had only `FastAPI(...)` + route decorators before this PR — no middleware stack to integrate with. Both middlewares were added at module import.
- `starlette.middleware.sessions.SessionMiddleware` — the Starlette built-in. Re-exported via FastAPI's `app.add_middleware(SessionMiddleware, …)`. Provides `request.session` as a dict-like backed by a signed cookie.
- `bcrypt` — added to `pyproject.toml` for hashing/verifying. Pure-Python is fine for single-user; the cost factor is whatever `bcrypt.gensalt()` defaults to (12 rounds at time of write).
- The `qbr` CLI is a Typer app in `src/qbr/cli.py`; adding `hash-password` was a one-decorator addition.
- Tests use `fastapi.testclient.TestClient` (sync wrapper). The auth env vars are set via `monkeypatch.setenv` per-test so the global `auth_enabled()` flips on for `auth_env`-fixtured tests only.

## Key Technical Decisions

- **Middleware ordering — registration vs. execution.** Starlette wraps middleware **outer-most-last**: middleware added later via `app.add_middleware(...)` becomes the **outer** layer. The plain `@app.middleware("http")` decorator registers an inner layer. So:
  - `@app.middleware("http")` registers `auth_middleware` first → it ends up **inside**.
  - `app.add_middleware(SessionMiddleware, …)` second → it ends up **outside**.
  - Net result: SessionMiddleware runs first (populating `request.session`), then auth_middleware runs and can read `request.session.get("user")`. The intuitive "session has to exist before auth checks it" ordering.
  - This non-obvious wrapping order is documented inline in the code.
- **Auth-off default.** `auth_enabled()` keys on the **presence** of `QBR_AUTH_PASSWORD_HASH`, not a separate boolean flag. Rationale: a config that has no hash cannot meaningfully verify anything, so the only safe interpretation is "off." Avoids a foot-gun where the operator sets a flag but forgets the hash and ends up with an unauthenticated open server.
- **bcrypt over argon2 / scrypt.** bcrypt's tooling (CLI hash + Python lib) is universally available; for a single-user PoC the marginal security gain of argon2 doesn't justify the extra dependency.
- **In-memory rate limit, not Redis.** The PoC is single-process; an in-memory `defaultdict[ip, list[float]]` is sufficient. The counter resets on process restart — accepted: a determined attacker can already trigger a restart by other means in this deployment context.
- **Login error returned with HTTP 401 status**, not a redirect-with-query-flag pattern. Rationale: the form is re-rendered server-side with the `error` context already set; 401 is the correct semantic and TestClient-friendly.
- **Public-path matcher splits exact paths from prefixes.** `PUBLIC_PATHS = {"/login", "/logout", "/healthz"}` (set lookup) + `PUBLIC_PREFIXES = ("/static/",)` (tuple for `startswith`). Rationale: `/static/foo.css` should match by prefix; `/login` should not match `/login-other`.
- **`request.client.host` may be `None`** behind some ASGI test setups → fall back to the literal string `"unknown"` so rate-limit accounting never raises.

## Implementation Units

- [x] **Unit 1 — `src/qbr_web/auth.py` helper module**

  **Goal:** Pure-function helpers for the whole auth surface so `app.py` only needs to wire them in.

  **Files:**
  - Create: `src/qbr_web/auth.py`
  - Modify: `pyproject.toml` (add `bcrypt`)

  **Approach:**
  - `auth_enabled()` — boolean check on `QBR_AUTH_PASSWORD_HASH`.
  - `get_session_secret()` — read `QBR_SESSION_SECRET` or generate a per-process `secrets.token_urlsafe(32)`.
  - `hash_password(plaintext)` — `bcrypt.hashpw(..., gensalt())` returning a UTF-8 string.
  - `verify_credentials(username, password)` — compare username constant-time-ish (string equality is fine for a single known username) and `bcrypt.checkpw` the password. Catches `ValueError` from malformed hashes and returns `False`.
  - `check_rate_limit(ip)` / `record_login_attempt(ip)` — in-memory `defaultdict` keyed by IP with sliding 15-min window.
  - `is_public_path(path)` — set + prefix tuple matcher.

  **Test scenarios (in `tests/test_auth.py`):**
  - `TestAuthHelpers::test_auth_disabled_by_default`, `test_auth_enabled_with_hash` — `auth_enabled()` switches on hash presence.
  - `TestAuthHelpers::test_hash_and_verify` — hash starts with `$2`.
  - `TestAuthHelpers::test_verify_valid_credentials`, `test_verify_wrong_password`, `test_verify_wrong_username`, `test_verify_no_config` — credential matrix.
  - `TestAuthHelpers::test_rate_limit_allows_initial_attempts` — first 5 attempts pass.
  - `TestAuthHelpers::test_is_public_path` — `/login`, `/logout`, `/healthz`, `/static/foo.css` public; `/`, `/analyze` not.

- [x] **Unit 2 — Middleware + login/logout routes in `src/qbr_web/app.py`**

  **Goal:** Wire the helpers into the FastAPI app so that, when auth is enabled, every non-public request without a session is bounced to `/login?next=…`.

  **Files:**
  - `src/qbr_web/app.py`
  - `src/qbr_web/templates/login.html`

  **Approach:**
  - Add `@app.middleware("http") async def auth_middleware(request, call_next)`. Short-circuits with `RedirectResponse("/login?next=…", 303)` when `auth_enabled() and not is_public_path(...) and not request.session.get("user")`.
  - Add `app.add_middleware(SessionMiddleware, secret_key=get_session_secret(), same_site="lax", https_only=False)` *after* the decorator so it wraps it (executes first).
  - `@app.get("/login")` — renders the template; if auth is disabled or session already valid, redirect to `next`.
  - `@app.post("/login")` — validates credentials, sets `request.session["user"]`, redirects to `next` on success; on rate-limit hit returns 429; on bad creds returns 401 with the template re-rendered carrying `error="Invalid credentials"`.
  - `@app.get("/logout")` and `@app.post("/logout")` (same handler) — clears session, redirects to `/login`.
  - Template: minimal Tailwind card (`max-w-md mx-auto mt-16`) with username/password fields, hidden `next` input, error banner block.

  **Test scenarios:**
  - `TestAuthDisabled::test_index_accessible`, `test_analyze_accessible` — with no env vars, `/` and `POST /analyze` work as before (no redirect).
  - `TestAuthEnabled::test_unauthenticated_redirects_to_login` — 303 to `/login?next=/`.
  - `TestAuthEnabled::test_healthz_still_public` — 200 with auth on.
  - `TestAuthEnabled::test_login_page_renders` — body contains "Sign In".
  - `TestAuthEnabled::test_valid_login` — 303 redirect to `/`.
  - `TestAuthEnabled::test_invalid_login_shows_error` — 401 + body contains "Invalid credentials".
  - `TestAuthEnabled::test_logout_clears_session` — login → logout → next request bounces to `/login`.

- [x] **Unit 3 — `qbr hash-password` CLI command**

  **Goal:** Operators can generate the bcrypt hash to paste into their `.env` without writing Python.

  **Files:**
  - `src/qbr/cli.py`

  **Approach:**
  - New Typer command `@app.command(name="hash-password")` that takes the plaintext as a positional arg, calls `qbr_web.auth.hash_password`, and prints the result with copy-paste-ready `.env` formatting via Rich.

  **Test scenarios:**
  - Functionally exercised by `TestAuthHelpers::test_hash_and_verify` (which calls the same `hash_password` function the CLI delegates to). The CLI wrapper is a one-liner with no logic of its own.

- [x] **Unit 4 — `.env.example` + REVIEW.md update**

  **Goal:** Make the auth config discoverable and close the security findings.

  **Files:**
  - `.env.example`
  - `REVIEW.md`

  **Approach:**
  - `.env.example` — append a `# === Web App Authentication (optional — leave unset to disable auth) ===` block with commented `QBR_AUTH_USER`, `QBR_AUTH_PASSWORD_HASH`, `QBR_SESSION_SECRET` examples and a `Generate hash:` hint pointing at the new CLI command.
  - `REVIEW.md` — flip findings S10 (no auth) and S11 (no CSRF) to `FIXED` with a one-line note pointing at the SameSite=Lax cookie + session middleware.

  **Test scenarios:** N/A — config + docs.

## System-Wide Impact

- **All existing tests continue to pass without modification** because auth is off by default. The auth tests turn it on per-test via the `auth_env` fixture and turn it off again by tearing down the `monkeypatch`.
- **Public-path whitelist is the security perimeter.** Any new route added later that should be reachable without login (e.g. a `/metrics` endpoint) must be added to `PUBLIC_PATHS` or `PUBLIC_PREFIXES`. The default-deny posture is the right one for a security feature, but reviewers of new routes need to know the rule.
- **Session secret rotation** invalidates every active session — operators should set `QBR_SESSION_SECRET` to a stable value in production; the auto-generated dev fallback regenerates per process.
- **`request.session` is a dict-like populated by SessionMiddleware** — code anywhere can `request.session.get("user")` without further imports. The dashboard plan (issue #45) and dedup plan (issue #36) both run inside the same middleware stack and inherit the auth gate transparently.
- **`https_only=False`** in the SessionMiddleware config is correct for local dev; production deployments behind HTTPS must override this (documented inline in code). No automated check enforces this — operator responsibility.

## Sources & References

- GitHub issue: [#35 — Single-user login page](https://github.com/peterkolcza/attrecto-qbr-task/issues/35)
- Pull request: [#40](https://github.com/peterkolcza/attrecto-qbr-task/pull/40) — commit `e2ef8f2`
- Code added/modified:
  - `src/qbr_web/auth.py` (new — 82 LOC)
  - `src/qbr_web/app.py` — middleware + login/logout routes
  - `src/qbr_web/templates/login.html` (new)
  - `src/qbr/cli.py` — `hash-password` Typer command
  - `pyproject.toml` — `bcrypt` dependency
  - `.env.example` — auth config block
  - `REVIEW.md` — S10/S11 → FIXED
- Tests: `tests/test_auth.py` (`TestAuthHelpers`, `TestAuthDisabled`, `TestAuthEnabled` — 18 cases).
- Related findings: REVIEW.md S10 (no auth), S11 (no CSRF — mitigated by SameSite=Lax).
- External: [Starlette SessionMiddleware](https://www.starlette.io/middleware/#sessionmiddleware), [bcrypt PyPI](https://pypi.org/project/bcrypt/).
