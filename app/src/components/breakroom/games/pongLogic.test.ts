import { describe, it, expect } from 'vitest'
import { paddleHit, reflectX, spinFromPaddle } from './pongLogic'

describe('paddleHit', () => {
  it('true when the ball is within the paddle span', () => {
    expect(paddleHit(50, 40, 20)).toBe(true) // 50 in [40,60]
  })
  it('false when the ball is above or below the paddle', () => {
    expect(paddleHit(30, 40, 20)).toBe(false)
    expect(paddleHit(70, 40, 20)).toBe(false)
  })
})

describe('reflectX', () => {
  it('reverses horizontal velocity', () => {
    expect(reflectX(5)).toBe(-5)
    expect(reflectX(-3)).toBe(3)
  })
})

describe('spinFromPaddle', () => {
  it('center contact imparts no vertical spin', () => {
    expect(spinFromPaddle(50, 40, 20, 6)).toBe(0) // center of [40,60] is 50
  })
  it('top edge sends the ball up, bottom edge down', () => {
    expect(spinFromPaddle(40, 40, 20, 6)).toBe(-6)
    expect(spinFromPaddle(60, 40, 20, 6)).toBe(6)
  })
})
