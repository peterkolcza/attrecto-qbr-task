# PDRW Plan — Issue #41 Tetris game

## Files
- NEW: `src/qbr_web/static/tetris.js` — vanilla JS Tetris (~300 lines)
- MOD: `src/qbr_web/templates/job.html` — add info + game containers, timeline JS

## Timeline on job page (only if job is processing/queued)
- t=0s: page loads, log container visible
- t=3s: fade in info message: "Email processing can take up to 10 minutes..."
- t=8s: fade in Tetris game (5s after message so user can read)
- when poll detects complete/error: stop game, hide containers

## Tetris features
- Canvas 200×400 (10 cols × 20 rows, 20px each)
- 7 tetrominoes (I, O, T, S, Z, J, L)
- Controls: ← → ↓ move, ↑ rotate, Space hard drop
- Score: 100/300/500/800 per 1/2/3/4 lines
- Speed up every 10 lines
- Next piece preview
- Game over → restart button
- Canvas gets keyboard focus on reveal

## Testing approach
- No Python tests needed (pure client-side JS)
- Manual: start processing → wait 3s for message, 8s for game → play
- Verify: game disappears when job completes

## Branch
`fix/41-tetris`
