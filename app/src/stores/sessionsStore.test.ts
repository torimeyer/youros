import { describe, it, expect, beforeEach } from 'vitest'
import { useSessionsStore } from './sessionsStore'

describe('useSessionsStore', () => {
  beforeEach(() => {
    useSessionsStore.setState({
      count: 0,
      sessions: [],
      connected: false,
    })
  })

  it('setSnapshot updates count and sessions', () => {
    const { setSnapshot } = useSessionsStore.getState()
    setSnapshot(2, [
      { session_id: 'claude-code-abc', last_active: '2026-05-10T00:00:00Z', status: 'active' },
      { session_id: 'agent-worker-1', last_active: '2026-05-10T00:00:01Z', status: 'idle' },
    ])
    const s = useSessionsStore.getState()
    expect(s.count).toBe(2)
    expect(s.sessions).toHaveLength(2)
  })

  it('setSnapshot with empty list sets count to 0', () => {
    const { setSnapshot } = useSessionsStore.getState()
    setSnapshot(0, [])
    expect(useSessionsStore.getState().count).toBe(0)
    expect(useSessionsStore.getState().sessions).toHaveLength(0)
  })

  it('setConnected flips connected flag', () => {
    const { setConnected } = useSessionsStore.getState()
    expect(useSessionsStore.getState().connected).toBe(false)
    setConnected(true)
    expect(useSessionsStore.getState().connected).toBe(true)
    setConnected(false)
    expect(useSessionsStore.getState().connected).toBe(false)
  })

  it('subsequent setSnapshot overwrites previous state', () => {
    const { setSnapshot } = useSessionsStore.getState()
    setSnapshot(3, [
      { session_id: 'a', last_active: '2026-05-10T00:00:00Z', status: 'active' },
      { session_id: 'b', last_active: '2026-05-10T00:00:01Z', status: 'active' },
      { session_id: 'c', last_active: '2026-05-10T00:00:02Z', status: 'idle' },
    ])
    setSnapshot(1, [{ session_id: 'a', last_active: '2026-05-10T00:00:00Z', status: 'active' }])
    const s = useSessionsStore.getState()
    expect(s.count).toBe(1)
    expect(s.sessions).toHaveLength(1)
  })
})
