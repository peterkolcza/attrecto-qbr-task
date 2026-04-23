---
title: "feat: Tetris game on job page — entertainment while waiting"
type: feat
status: completed
date: 2026-04-23
retro: true
origin: "GitHub issue #41"
shipped_in: "PR #42 (commit 16db6dd)"
---

# feat: Tetris game on job page — entertainment while waiting

## Overview

Embed a self-contained, vanilla-JS Tetris game on the job detail page so users have something to do during the ~10-minute local-Ollama analysis run. After page load, a friendly info message fades in at t=3s telling the user the run can take up to 10 minutes (and that they can leave the page); at t=8s the game itself fades in, takes keyboard focus, and starts. The game stops automatically when the job reaches a terminal state.

## Problem Frame

With the local `gemma3:e2b` Ollama model the demo run takes around 10 minutes for 18 emails. During that time the only thing on the job page is a slowly-growing log. Two distinct problems:

1. **Transparency.** A visitor doesn't know how long to wait. They might think the page is hung.
2. **Delight.** The Attrecto evaluator will demo the system live; a small, well-built game is a memorable UX detail and shows attention to product polish — explicitly aligned with the "mindset, approach, structure" grading axis in the task brief.

The fix is intentionally pure-frontend: no server changes, no game library, no persistence. It's a delight feature; it must not introduce risk to the actual pipeline.

Origin: GitHub issue #41.

## Requirements Trace

- R1. After 3s, an English info message appears explaining the wait and pointing back to the dashboard. **DONE** — `#wait-info` div revealed via `setTimeout(..., 3000)`.
- R2. After 8s total (3s + 5s read time), the Tetris game fades in below the info message. **DONE** — second `setTimeout(..., 8000)` reveals `#tetris-container` and calls `startGame()`.
- R3. Standard 10×20 Tetris grid with all 7 tetrominoes (I/O/T/S/Z/J/L). **DONE** — `COLS=10`, `ROWS=20`, full `SHAPES` table in `tetris.js`.
- R4. Controls: arrow keys for move/rotate, space for hard drop. **DONE** — keyboard handler in `tetris.js`.
- R5. Score, lines cleared, and next-piece preview are displayed. **DONE** — `#tetris-score`, `#tetris-lines`, `#tetris-next` canvas elements.
- R6. Game-over detection with a Restart button. **DONE** — `#tetris-gameover` block with `onclick="window.__tetrisRestart()"`.
- R7. Game appears only when the job is `processing` or `queued`; auto-stops on `complete`/`error`. **DONE** — `{% if job.state in ('processing', 'queued') %}` gates the markup; the polling block calls `window.__stopTetris()` when transitioning to `complete` or `error`.
- R8. Pure vanilla JS, no external library. **DONE** — `src/qbr_web/static/tetris.js` is self-contained; no script tags added except its own.
- R9. Tailwind-styled to match the dashboard look. **DONE** — cyan accent, rounded corners, gray-900 canvas background.

## Scope Boundaries

- No persistence of high scores — the game resets on every page load.
- No mobile/touch controls — the keyboard-only assumption is acceptable for a desktop-evaluator demo.
- No sound effects or music.
- No "pause" button — the game pauses naturally when the user navigates away or the job completes.
- No accessibility audit beyond keyboard focus on reveal — the game is a non-essential entertainment widget; the underlying job page remains fully usable without it.

## Context & Research

### Relevant Code and Patterns

- `src/qbr_web/templates/job.html` — right column "Processing..." section is the natural anchor; the new markup goes directly underneath it inside the `{% if job.state in ('processing', 'queued') %}` block.
- `src/qbr_web/static/` — directory previously held only a `.gitkeep`; this is the first real static asset.
- Existing job-page polling block in `job.html` — already inspects `data.state` on every poll; the cleanest hook for stopping the game is to call a `window.__stopTetris()` from inside the `complete` and `error` branches.
- `tests/conftest.py` — auth env vars are not disabled by default in tests, so adding any new test that hits the job page would 401. Same commit added a `conftest.py` fixture that clears them per session.

## Key Technical Decisions

- **Single self-contained module exposing `window.QBRTetris.{start, stop}`.** Rationale: avoids bundling, keeps the surface tiny, and lets the inline reveal script in `job.html` orchestrate timing without coupling to game internals.
- **Reveal timing as two `setTimeout` calls in an IIFE in the template, not inside `tetris.js`.** Rationale: the game module shouldn't know about the page's "wait info" UX; the template owns the reveal choreography.
- **Tailwind opacity transition (`opacity-0` → `opacity-100`) inside a `requestAnimationFrame`.** Rationale: removing `hidden` and changing opacity in the same tick skips the transition; one rAF separation is enough to trigger the fade.
- **Stop game by hiding the container, not destroying state.** Rationale: simpler than tearing down the canvas; the page reloads after `complete` anyway, and the polling-driven stop is a defensive measure.
- **Auth-disabling `conftest.py` introduced alongside this PR.** Rationale: the game itself doesn't need auth changes, but the new tests added to `tests/test_web.py` for the job page would otherwise 401 against the auth middleware introduced earlier. Bundling the fixture into this PR avoids a second cleanup commit.
- **Hard-coded 3s/8s reveal timings.** Rationale: usability sweet spot found by manual feel-testing; making it configurable adds a settings surface for a delight feature, which is the wrong direction.

## Implementation Units

- [x] **Unit 1: Self-contained Tetris module**

  **Goal:** A single file `src/qbr_web/static/tetris.js` that implements a complete playable Tetris and exposes `window.QBRTetris.start(canvas, nextCanvas, scoreEl, linesEl, onGameOver)` and `window.QBRTetris.stop()`.

  **Files:**
  - `src/qbr_web/static/tetris.js` (new)

  **Approach:** Pure JS module wrapped in an IIFE. Constants `COLS=10`, `ROWS=20`, `CELL=20`. `SHAPES` table holding all rotations of the 7 tetrominoes plus a color map. `newGrid()`, `randomPiece()`, `canPlace()`, `merge()`, `clearLines()` helpers; a `tick()` that drops the active piece on a setInterval; a `keydown` handler for movement, rotation, and hard drop; a `draw()` routine that renders the grid + active piece + next-piece preview onto two canvases. Scoring: 100/300/500/800 per 1/2/3/4 lines cleared. Game-over fires the `onGameOver` callback so the template can show the restart button.

  **Verification:** Open the job page in the browser, wait 8s, play. Verify all 7 pieces appear, lines clear, score increments, game-over triggers on stack overflow.

- [x] **Unit 2: Job page markup + reveal choreography**

  **Goal:** Add the wait-info banner, the game container (canvas + side panel with score/lines/next/restart), and the JS that reveals them on the 3s / 8s timeline. Wire the polling loop to stop the game on terminal state.

  **Files:**
  - `src/qbr_web/templates/job.html`

  **Approach:** Inside the existing `{% if job.state in ('processing', 'queued') %}` block, add `#wait-info` (cyan banner) and `#tetris-container` (game UI), both starting `hidden opacity-0` with a `transition-opacity` class. Below the closing `</div>`, add `<script src="/static/tetris.js">` and a sibling IIFE that:
  - At t=3000ms, removes `hidden` from `#wait-info` and swaps `opacity-0` → `opacity-100` inside an rAF.
  - At t=8000ms, does the same for `#tetris-container` and calls `startGame()`.
  - Defines `window.__tetrisRestart` (called by the in-template Restart button) and `window.__stopTetris` (called by the polling block).

  In the existing polling block, in both the `data.state === 'complete'` and `data.state === 'error'` branches, call `window.__stopTetris && window.__stopTetris()` before the existing badge updates.

  **Verification:** Trigger a demo run; verify the info appears at ~3s, the game appears and is focused at ~8s, and the game disappears as soon as the polled state flips to `complete`.

- [x] **Unit 3: Test fixture for auth-protected pages**

  **Goal:** Allow the existing test suite to keep hitting authed routes (incl. the job page where the game lives) without configuring credentials per test.

  **Files:**
  - `tests/conftest.py` (new)

  **Approach:** Add a session-scoped autouse fixture that pops the auth-related env vars (or sets them to known empty values) so the auth middleware no-ops in tests.

  **Verification:** `make test` passes (149 tests at the time of the PR).

## Sources & References

- GitHub issue #41, PR #42, commit `16db6dd`
- Affected files:
  - `src/qbr_web/static/tetris.js` (new)
  - `src/qbr_web/templates/job.html`
  - `tests/conftest.py` (new)
- Adjacent plans: `docs/plans/2026-04-23-018-feat-verbose-processing-log-plan.md` (precursor — same job page, different concern)
