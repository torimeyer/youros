// Pure logic for Minesweeper. No React, no DOM, unit-testable headless.

export interface Cell {
  mine: boolean
  adj: number
  revealed: boolean
  flagged: boolean
}

export function createBoard(rows: number, cols: number, mines: [number, number][]): Cell[][] {
  const mineSet = new Set(mines.map(([r, c]) => r + ',' + c))
  const board: Cell[][] = []
  for (let r = 0; r < rows; r++) {
    const row: Cell[] = []
    for (let c = 0; c < cols; c++) {
      row.push({ mine: mineSet.has(r + ',' + c), adj: 0, revealed: false, flagged: false })
    }
    board.push(row)
  }
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (board[r][c].mine) continue
      let n = 0
      for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue
          const nr = r + dr
          const nc = c + dc
          if (nr >= 0 && nr < rows && nc >= 0 && nc < cols && board[nr][nc].mine) n++
        }
      }
      board[r][c].adj = n
    }
  }
  return board
}

// Random mine placement. When `safe` is given, the clicked cell AND its full
// 3x3 neighborhood are kept mine-free, so the first click always opens an area
// instead of a lone number (classic first-click safety).
export function randomMines(
  rows: number,
  cols: number,
  count: number,
  rng: () => number = Math.random,
  safe?: [number, number],
): [number, number][] {
  const cells: [number, number][] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (safe && Math.abs(r - safe[0]) <= 1 && Math.abs(c - safe[1]) <= 1) continue
      cells.push([r, c])
    }
  }
  for (let i = cells.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    ;[cells[i], cells[j]] = [cells[j], cells[i]]
  }
  return cells.slice(0, Math.min(count, cells.length))
}

// Reveal a cell with flood fill. Returns a new board (immutable).
export function floodReveal(board: Cell[][], sr: number, sc: number): Cell[][] {
  const rows = board.length
  const cols = board[0].length
  const next = board.map((row) => row.map((cell) => ({ ...cell })))
  const start = next[sr][sc]
  if (start.flagged || start.revealed) return next
  if (start.mine) {
    start.revealed = true
    return next
  }
  const stack: [number, number][] = [[sr, sc]]
  while (stack.length) {
    const [r, c] = stack.pop()!
    const cell = next[r][c]
    if (cell.revealed || cell.flagged || cell.mine) continue
    cell.revealed = true
    if (cell.adj === 0) {
      for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue
          const nr = r + dr
          const nc = c + dc
          if (nr >= 0 && nr < rows && nc >= 0 && nc < cols) {
            const nb = next[nr][nc]
            if (!nb.revealed && !nb.flagged && !nb.mine) stack.push([nr, nc])
          }
        }
      }
    }
  }
  return next
}

export function toggleFlag(board: Cell[][], r: number, c: number): Cell[][] {
  return board.map((row, ri) =>
    row.map((cell, ci) => (ri === r && ci === c && !cell.revealed ? { ...cell, flagged: !cell.flagged } : cell)),
  )
}

// Win when every non-mine cell has been revealed.
export function isWin(board: Cell[][]): boolean {
  return board.every((row) => row.every((cell) => cell.mine || cell.revealed))
}

export function countRevealed(board: Cell[][]): number {
  let n = 0
  for (const row of board) for (const cell of row) if (cell.revealed) n++
  return n
}

export function countFlags(board: Cell[][]): number {
  let n = 0
  for (const row of board) for (const cell of row) if (cell.flagged) n++
  return n
}
