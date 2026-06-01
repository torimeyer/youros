// Pure logic for Cats & Snakes. No React, no DOM, unit-testable headless.
// Mirrors the snakeLogic.ts pattern.

// Cat squares: land here and your token rides UP to the destination.
export const CATS: Record<number, number> = Object.freeze({
  1: 38,
  4: 14,
  9: 31,
  20: 38,
  28: 84,
  40: 59,
  51: 67,
  63: 81,
  71: 91,
})

// Snake squares: land here and your token slides DOWN to the destination.
export const SNAKES: Record<number, number> = Object.freeze({
  17: 7,
  54: 34,
  62: 19,
  64: 60,
  87: 24,
  93: 73,
  95: 75,
  99: 78,
})

export function rollDie(): number {
  return Math.floor(Math.random() * 6) + 1
}

// Advance `pos` by `roll`, apply overshoot bounce at 100, then follow any
// cat or snake on the landing square.
export function applyMove(pos: number, roll: number): number {
  let next = pos + roll
  if (next > 100) {
    next = 100 - (next - 100)
  }
  return CATS[next] ?? SNAKES[next] ?? next
}

export function isWin(pos: number): boolean {
  return pos === 100
}

export interface GridPos {
  row: number // 0 = top row
  col: number // 0 = leftmost column
}

// Maps a square number (1–100) to its grid position in snake order.
// Rows 0–9 top-to-bottom; even rows from bottom go left-to-right,
// odd rows from bottom go right-to-left.
export function squareToGrid(sq: number): GridPos {
  const rowFromBottom = Math.floor((sq - 1) / 10)
  const posInRow = (sq - 1) % 10
  const col = rowFromBottom % 2 === 0 ? posInRow : 9 - posInRow
  return { row: 9 - rowFromBottom, col }
}
