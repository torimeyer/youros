import { describe, it, expect } from 'vitest'
import { frogStatus, reachedHome, type Lane } from './froggerLogic'

const lane = (type: Lane['type'], occupied: number[]): Lane => ({ type, occupied })

describe('frogStatus', () => {
  it('road: dies when a car occupies the cell', () => {
    expect(frogStatus(3, lane('road', [3, 7])).dead).toBe(true)
  })
  it('road: safe on an empty cell', () => {
    expect(frogStatus(4, lane('road', [3, 7])).dead).toBe(false)
  })
  it('river: drowns when no log is under the frog', () => {
    const st = frogStatus(5, lane('river', [0, 1, 2]))
    expect(st.dead).toBe(true)
    expect(st.onLog).toBe(false)
  })
  it('river: safe and riding when standing on a log', () => {
    const st = frogStatus(1, lane('river', [0, 1, 2]))
    expect(st.dead).toBe(false)
    expect(st.onLog).toBe(true)
  })
  it('safe lane: never dead', () => {
    expect(frogStatus(5, lane('safe', [])).dead).toBe(false)
  })
})

describe('reachedHome', () => {
  it('true at the top row', () => {
    expect(reachedHome(0, 0)).toBe(true)
    expect(reachedHome(3, 0)).toBe(false)
  })
})
