import { describe, it, expect } from 'vitest'
import { rollDie, applyMove, isWin, squareToGrid, CATS, SNAKES } from './catsAndSnakesLogic'

describe('rollDie', () => {
  it('always returns 1–6', () => {
    for (let i = 0; i < 500; i++) {
      const r = rollDie()
      expect(r).toBeGreaterThanOrEqual(1)
      expect(r).toBeLessThanOrEqual(6)
    }
  })
})

describe('applyMove', () => {
  it('advances by roll on a plain square', () => {
    expect(applyMove(5, 3)).toBe(8)
  })

  it('snake slide: landing on a snake square slides down', () => {
    // Square 17 is a snake → 7.  15+2=17.
    expect(applyMove(15, 2)).toBe(SNAKES[17])
  })

  it('cat climb: landing on a cat square rides up', () => {
    // Square 4 is a cat → 14.  3+1=4.
    expect(applyMove(3, 1)).toBe(CATS[4])
  })

  it('exact landing at 100 wins', () => {
    expect(applyMove(97, 3)).toBe(100)
  })

  it('overshoot from 100 bounces back', () => {
    // 98+4=102 → bounce to 98
    expect(applyMove(98, 4)).toBe(98)
  })

  it('overshoot and then follow a snake on the bounced square', () => {
    // Need a square that bounces to a snake.
    // 99 is a snake (→78). Roll 2 from 99: 99+2=101 → bounce to 99 → snake 78.
    expect(applyMove(99, 2)).toBe(SNAKES[99])
  })

  it('does not move past 100 without bouncing', () => {
    const result = applyMove(95, 6)
    expect(result).toBeLessThanOrEqual(100)
    expect(result).toBeGreaterThanOrEqual(1)
  })
})

describe('isWin', () => {
  it('is true at 100', () => expect(isWin(100)).toBe(true))
  it('is false at 99', () => expect(isWin(99)).toBe(false))
  it('is false at 1', () => expect(isWin(1)).toBe(false))
})

describe('squareToGrid', () => {
  it('square 1 is bottom-left (row 9, col 0)', () => {
    expect(squareToGrid(1)).toEqual({ row: 9, col: 0 })
  })
  it('square 10 is bottom-right (row 9, col 9)', () => {
    expect(squareToGrid(10)).toEqual({ row: 9, col: 9 })
  })
  it('square 11 is one row up, far right (row 8, col 9) — snake-order', () => {
    expect(squareToGrid(11)).toEqual({ row: 8, col: 9 })
  })
  it('square 20 is one row up, far left (row 8, col 0)', () => {
    expect(squareToGrid(20)).toEqual({ row: 8, col: 0 })
  })
  it('square 100 is top-left (row 0, col 0)', () => {
    expect(squareToGrid(100)).toEqual({ row: 0, col: 0 })
  })
  it('square 91 is top-right (row 0, col 9)', () => {
    expect(squareToGrid(91)).toEqual({ row: 0, col: 9 })
  })
})

describe('CATS and SNAKES maps', () => {
  it('all cat destinations are higher than their source', () => {
    for (const [src, dst] of Object.entries(CATS)) {
      expect(dst).toBeGreaterThan(Number(src))
    }
  })
  it('all snake destinations are lower than their source', () => {
    for (const [src, dst] of Object.entries(SNAKES)) {
      expect(dst).toBeLessThan(Number(src))
    }
  })
  it('no square appears in both CATS and SNAKES', () => {
    const catSquares = new Set(Object.keys(CATS))
    const snakeSquares = Object.keys(SNAKES)
    for (const s of snakeSquares) {
      expect(catSquares.has(s)).toBe(false)
    }
  })
})
