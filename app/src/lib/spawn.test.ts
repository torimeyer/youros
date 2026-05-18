import { describe, it, expect } from 'vitest'
import { buildSpec } from './spawn'

describe('spawn — scaffold', () => {
  it('exports buildSpec', () => {
    expect(typeof buildSpec).toBe('function')
  })
})
