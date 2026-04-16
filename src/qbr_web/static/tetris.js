// Tetris — self-contained vanilla JS implementation.
// Usage: window.QBRTetris.start(canvasElem, nextCanvasElem, scoreElem, linesElem, onGameOver)

(function () {
  const COLS = 10;
  const ROWS = 20;
  const CELL = 20;

  const COLORS = {
    0: '#111827', // empty (gray-900)
    1: '#06b6d4', // I — cyan
    2: '#eab308', // O — yellow
    3: '#a855f7', // T — purple
    4: '#22c55e', // S — green
    5: '#ef4444', // Z — red
    6: '#3b82f6', // J — blue
    7: '#f97316', // L — orange
  };

  // Tetromino shapes: each is [rotations] where each rotation is a 4x4 matrix
  const SHAPES = [
    null,
    // I
    [[[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]],
     [[0,0,1,0],[0,0,1,0],[0,0,1,0],[0,0,1,0]],
     [[0,0,0,0],[0,0,0,0],[1,1,1,1],[0,0,0,0]],
     [[0,1,0,0],[0,1,0,0],[0,1,0,0],[0,1,0,0]]],
    // O
    [[[0,2,2,0],[0,2,2,0],[0,0,0,0],[0,0,0,0]]],
    // T
    [[[0,3,0,0],[3,3,3,0],[0,0,0,0],[0,0,0,0]],
     [[0,3,0,0],[0,3,3,0],[0,3,0,0],[0,0,0,0]],
     [[0,0,0,0],[3,3,3,0],[0,3,0,0],[0,0,0,0]],
     [[0,3,0,0],[3,3,0,0],[0,3,0,0],[0,0,0,0]]],
    // S
    [[[0,4,4,0],[4,4,0,0],[0,0,0,0],[0,0,0,0]],
     [[4,0,0,0],[4,4,0,0],[0,4,0,0],[0,0,0,0]]],
    // Z
    [[[5,5,0,0],[0,5,5,0],[0,0,0,0],[0,0,0,0]],
     [[0,0,5,0],[0,5,5,0],[0,5,0,0],[0,0,0,0]]],
    // J
    [[[6,0,0,0],[6,6,6,0],[0,0,0,0],[0,0,0,0]],
     [[0,6,6,0],[0,6,0,0],[0,6,0,0],[0,0,0,0]],
     [[0,0,0,0],[6,6,6,0],[0,0,6,0],[0,0,0,0]],
     [[0,6,0,0],[0,6,0,0],[6,6,0,0],[0,0,0,0]]],
    // L
    [[[0,0,7,0],[7,7,7,0],[0,0,0,0],[0,0,0,0]],
     [[0,7,0,0],[0,7,0,0],[0,7,7,0],[0,0,0,0]],
     [[0,0,0,0],[7,7,7,0],[7,0,0,0],[0,0,0,0]],
     [[7,7,0,0],[0,7,0,0],[0,7,0,0],[0,0,0,0]]],
  ];

  let game = null;

  function newGrid() {
    return Array.from({ length: ROWS }, () => Array(COLS).fill(0));
  }

  function randomPiece() {
    const type = 1 + Math.floor(Math.random() * 7);
    return { type, rot: 0, x: 3, y: 0 };
  }

  function getShape(piece) {
    const rots = SHAPES[piece.type];
    return rots[piece.rot % rots.length];
  }

  function canPlace(grid, piece, dx, dy, drot) {
    const rots = SHAPES[piece.type];
    const shape = rots[(piece.rot + (drot || 0) + rots.length) % rots.length];
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        if (!shape[r][c]) continue;
        const nx = piece.x + c + dx;
        const ny = piece.y + r + dy;
        if (nx < 0 || nx >= COLS || ny >= ROWS) return false;
        if (ny >= 0 && grid[ny][nx]) return false;
      }
    }
    return true;
  }

  function merge(grid, piece) {
    const shape = getShape(piece);
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        if (shape[r][c] && piece.y + r >= 0) {
          grid[piece.y + r][piece.x + c] = shape[r][c];
        }
      }
    }
  }

  function clearLines(grid) {
    let cleared = 0;
    for (let r = ROWS - 1; r >= 0; r--) {
      if (grid[r].every((v) => v !== 0)) {
        grid.splice(r, 1);
        grid.unshift(Array(COLS).fill(0));
        cleared++;
        r++;
      }
    }
    return cleared;
  }

  function drawCell(ctx, x, y, color) {
    ctx.fillStyle = color;
    ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 1;
    ctx.strokeRect(x * CELL, y * CELL, CELL, CELL);
  }

  function drawGrid(ctx, grid) {
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, COLS * CELL, ROWS * CELL);
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        if (grid[r][c]) drawCell(ctx, c, r, COLORS[grid[r][c]]);
      }
    }
  }

  function drawPiece(ctx, piece) {
    const shape = getShape(piece);
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        if (shape[r][c] && piece.y + r >= 0) {
          drawCell(ctx, piece.x + c, piece.y + r, COLORS[shape[r][c]]);
        }
      }
    }
  }

  function drawNext(ctx, piece) {
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, 4 * CELL, 4 * CELL);
    if (!piece) return;
    const shape = SHAPES[piece.type][0];
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        if (shape[r][c]) drawCell(ctx, c, r, COLORS[shape[r][c]]);
      }
    }
  }

  function start(canvas, nextCanvas, scoreEl, linesEl, onGameOver) {
    if (game) stop();

    const ctx = canvas.getContext('2d');
    const nextCtx = nextCanvas.getContext('2d');

    const state = {
      grid: newGrid(),
      current: randomPiece(),
      next: randomPiece(),
      score: 0,
      lines: 0,
      level: 1,
      dropInterval: 800,
      lastDrop: 0,
      gameOver: false,
      animFrame: null,
      keyHandler: null,
    };

    function render() {
      drawGrid(ctx, state.grid);
      if (!state.gameOver) drawPiece(ctx, state.current);
      drawNext(nextCtx, state.next);
      if (scoreEl) scoreEl.textContent = state.score;
      if (linesEl) linesEl.textContent = state.lines;
    }

    function lockAndNext() {
      merge(state.grid, state.current);
      const cleared = clearLines(state.grid);
      if (cleared) {
        const points = [0, 100, 300, 500, 800][cleared];
        state.score += points * state.level;
        state.lines += cleared;
        state.level = 1 + Math.floor(state.lines / 10);
        state.dropInterval = Math.max(80, 800 - (state.level - 1) * 60);
      }
      state.current = state.next;
      state.next = randomPiece();
      if (!canPlace(state.grid, state.current, 0, 0, 0)) {
        state.gameOver = true;
        render();
        if (onGameOver) onGameOver(state.score, state.lines);
      }
    }

    function tick(ts) {
      if (state.gameOver) return;
      if (!state.lastDrop) state.lastDrop = ts;
      if (ts - state.lastDrop >= state.dropInterval) {
        if (canPlace(state.grid, state.current, 0, 1, 0)) {
          state.current.y++;
        } else {
          lockAndNext();
        }
        state.lastDrop = ts;
      }
      render();
      state.animFrame = requestAnimationFrame(tick);
    }

    function handleKey(e) {
      if (state.gameOver) return;
      switch (e.key) {
        case 'ArrowLeft':
          if (canPlace(state.grid, state.current, -1, 0, 0)) state.current.x--;
          e.preventDefault();
          break;
        case 'ArrowRight':
          if (canPlace(state.grid, state.current, 1, 0, 0)) state.current.x++;
          e.preventDefault();
          break;
        case 'ArrowDown':
          if (canPlace(state.grid, state.current, 0, 1, 0)) {
            state.current.y++;
            state.score += 1;
          }
          e.preventDefault();
          break;
        case 'ArrowUp':
          if (canPlace(state.grid, state.current, 0, 0, 1)) state.current.rot++;
          e.preventDefault();
          break;
        case ' ':
          while (canPlace(state.grid, state.current, 0, 1, 0)) {
            state.current.y++;
            state.score += 2;
          }
          lockAndNext();
          e.preventDefault();
          break;
      }
      render();
    }

    state.keyHandler = handleKey;
    document.addEventListener('keydown', state.keyHandler);
    canvas.tabIndex = 0;
    canvas.focus();

    state.animFrame = requestAnimationFrame(tick);
    game = state;
    render();
  }

  function stop() {
    if (!game) return;
    if (game.animFrame) cancelAnimationFrame(game.animFrame);
    if (game.keyHandler) document.removeEventListener('keydown', game.keyHandler);
    game = null;
  }

  window.QBRTetris = { start, stop };
})();
