import { describe, it, expect } from 'vitest'
import { scorePegs, isWin } from './mastermindLogic'

describe('scorePegs', () => {
  it('all correct = all exact', () => {
    expect(scorePegs(['A', 'B', 'C', 'D'], ['A', 'B', 'C', 'D'])).toEqual({ exact: 4, partial: 0 })
  })
  it('full reversal = all partial', () => {
    expect(scorePegs(['A', 'B', 'C', 'D'], ['D', 'C', 'B', 'A'])).toEqual({ exact: 0, partial: 4 })
  })
  it('handles duplicates without double-counting (secret AABB, guess ABAB)', () => {
    expect(scorePegs(['A', 'A', 'B', 'B'], ['A', 'B', 'A', 'B'])).toEqual({ exact: 2, partial: 2 })
  })
  it('extra guess copies of a color are not over-credited (secret AABB, guess AACC)', () => {
    expect(scorePegs(['A', 'A', 'B', 'B'], ['A', 'A', 'C', 'C'])).toEqual({ exact: 2, partial: 0 })
  })
  it('no matches', () => {
    expect(scorePegs(['A', 'A', 'A', 'A'], ['B', 'B', 'B', 'B'])).toEqual({ exact: 0, partial: 0 })
  })
})

describe('isWin', () => {
  it('wins when exact equals code length', () => {
    expect(isWin({ exact: 4, partial: 0 }, 4)).toBe(true)
    expect(isWin({ exact: 3, partial: 1 }, 4)).toBe(false)
  })
})
