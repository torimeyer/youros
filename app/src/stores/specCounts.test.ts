import { describe, it, expect, beforeEach } from 'vitest'
import { useSpecCountsStore } from './specCounts'

describe('useSpecCountsStore', () => {
  beforeEach(() => {
    useSpecCountsStore.setState({
      unfinished: 0,
      total: 0,
      connected: false,
      lastUpdatedAt: null,
    })
  })

  it('setSnapshot updates unfinished and total', () => {
    const { setSnapshot } = useSpecCountsStore.getState()
    setSnapshot(3, 5)
    const s = useSpecCountsStore.getState()
    expect(s.unfinished).toBe(3)
    expect(s.total).toBe(5)
    expect(s.lastUpdatedAt).not.toBeNull()
  })

  it('setSnapshot with zeros clears counts', () => {
    const { setSnapshot } = useSpecCountsStore.getState()
    setSnapshot(3, 5)
    setSnapshot(0, 0)
    const s = useSpecCountsStore.getState()
    expect(s.unfinished).toBe(0)
    expect(s.total).toBe(0)
  })

  it('setConnected flips the connected flag', () => {
    const { setConnected } = useSpecCountsStore.getState()
    expect(useSpecCountsStore.getState().connected).toBe(false)
    setConnected(true)
    expect(useSpecCountsStore.getState().connected).toBe(true)
    setConnected(false)
    expect(useSpecCountsStore.getState().connected).toBe(false)
  })

  it('subsequent setSnapshot overwrites previous state', () => {
    const { setSnapshot } = useSpecCountsStore.getState()
    setSnapshot(2, 4)
    setSnapshot(1, 3)
    const s = useSpecCountsStore.getState()
    expect(s.unfinished).toBe(1)
    expect(s.total).toBe(3)
  })
})
