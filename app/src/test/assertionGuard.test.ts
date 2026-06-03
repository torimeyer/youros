/**
 * Tests for the assertion-free test guard (→2067).
 *
 * Verifies that vitest's expect.getState().assertionCalls resets to 0 at the
 * start of each test and increments with each expect() call — which is the
 * mechanism the afterEach guard in setup.ts relies on.
 */

describe('assertion guard', () => {
  it('assertionCalls starts at 0 for a fresh test', () => {
    const callsAtStart = expect.getState().assertionCalls
    expect(callsAtStart).toBe(0)
  })

  it('assertionCalls increments once per expect() call', () => {
    expect(1 + 1).toBe(2)          // call #1
    expect(true).toBe(true)         // call #2
    expect(expect.getState().assertionCalls).toBe(3)  // call #3 — should now be 3
  })

  it('assertionCalls resets to 0 between tests (this test proves the previous did not bleed)', () => {
    const fresh = expect.getState().assertionCalls
    expect(fresh).toBe(0)
  })
})
