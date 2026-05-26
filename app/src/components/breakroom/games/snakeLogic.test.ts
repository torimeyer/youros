import { describe, it, expect } from 'vitest'
import { nextHead, detectCollision, step, type Pt } from './snakeLogic'

const p = (x: number, y: number): Pt => ({ x, y })

describe('nextHead', () => {
  it('advances by the direction vector', () => {
    expect(nextHead(p(1, 1), p(1, 0))).toEqual(p(2, 1))
    expect(nextHead(p(1, 1), p(0, -1))).toEqual(p(1, 0))
  })
})

describe('detectCollision', () => {
  it('collides with a body segment', () => {
    expect(detectCollision(p(2, 2), [p(2, 2), p(2, 3)], 10, 10)).toBe(true)
  })
  it('collides with a wall (out of bounds)', () => {
    expect(detectCollision(p(-1, 5), [], 10, 10)).toBe(true)
    expect(detectCollision(p(10, 5), [], 10, 10)).toBe(true)
  })
  it('no collision on an empty in-bounds cell', () => {
    expect(detectCollision(p(5, 5), [p(1, 1)], 10, 10)).toBe(false)
  })
})

describe('step', () => {
  it('grows the snake when it eats food', () => {
    const r = step([p(1, 1)], p(1, 0), p(2, 1), 10, 10)
    expect(r.dead).toBe(false)
    expect(r.ate).toBe(true)
    expect(r.snake).toEqual([p(2, 1), p(1, 1)])
  })
  it('moves without growing when there is no food', () => {
    const r = step([p(1, 1)], p(1, 0), p(9, 9), 10, 10)
    expect(r.ate).toBe(false)
    expect(r.snake).toEqual([p(2, 1)])
  })
  it('dies when it runs into the wall', () => {
    const r = step([p(9, 5)], p(1, 0), p(0, 0), 10, 10)
    expect(r.dead).toBe(true)
  })
})
