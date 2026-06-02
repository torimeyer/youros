import { describe, it, expect } from 'vitest'
import { createBoard, floodReveal, isWin, countRevealed, randomMines } from './minesweeperLogic'

// 3x3 board with a single mine at (0,0).
const board = () => createBoard(3, 3, [[0, 0]])

describe('createBoard adjacency', () => {
  it('computes adjacent mine counts', () => {
    const b = board()
    expect(b[0][0].mine).toBe(true)
    expect(b[0][1].adj).toBe(1)
    expect(b[1][1].adj).toBe(1)
    expect(b[2][2].adj).toBe(0)
  })
})

describe('floodReveal', () => {
  it('cascades from a zero cell, stops at numbered borders, never reveals the mine', () => {
    const b = floodReveal(board(), 2, 2)
    expect(b[2][2].revealed).toBe(true)
    expect(b[1][1].revealed).toBe(true)
    expect(b[0][0].revealed).toBe(false)
    expect(countRevealed(b)).toBe(8)
  })
  it('never reveals a flagged cell', () => {
    let b = board()
    b = b.map((row, r) => row.map((cell, c) => (r === 0 && c === 2 ? { ...cell, flagged: true } : cell)))
    b = floodReveal(b, 2, 2)
    expect(b[0][2].revealed).toBe(false)
  })
  it('revealing the mine reveals only the mine', () => {
    const b = floodReveal(board(), 0, 0)
    expect(b[0][0].revealed).toBe(true)
    expect(countRevealed(b)).toBe(1)
  })
})

describe('randomMines first-click safety', () => {
  it('keeps the clicked cell and its full 3x3 neighborhood mine-free', () => {
    const mines = randomMines(16, 16, 40, Math.random, [8, 8])
    expect(mines).toHaveLength(40)
    for (const [r, c] of mines) {
      const near = Math.abs(r - 8) <= 1 && Math.abs(c - 8) <= 1
      expect(near).toBe(false)
    }
  })
})

describe('isWin', () => {
  it('wins when every non-mine cell is revealed', () => {
    expect(isWin(floodReveal(board(), 2, 2))).toBe(true)
  })
  it('is not won initially', () => {
    expect(isWin(board())).toBe(false)
  })
})
