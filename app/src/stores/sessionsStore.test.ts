import { describe, it, expect, beforeEach } from 'vitest'
import { useSessionsStore } from './sessionsStore'

const SESSION = {
  id: 's-1',
  name: 'pclaude',
  type: 'claude-code',
  started_at: '2026-05-10T00:00:00Z',
  last_active_at: '2026-05-10T00:05:00Z',
  status: 'active' as const,
}

describe('useSessionsStore', () => {
  beforeEach(() => {
    useSessionsStore.setState({
      sessions: [],
      locks: [],
      events: [],
      connected: false,
      lastUpdatedAt: null,
    })
  })

  it('setSnapshot stores sessions, locks and events', () => {
    useSessionsStore.getState().setSnapshot([SESSION], [], [])
    expect(useSessionsStore.getState().sessions).toHaveLength(1)
    expect(useSessionsStore.getState().lastUpdatedAt).not.toBeNull()
  })

  it('content-equal snapshot keeps the same references (→1730)', () => {
    useSessionsStore.getState().setSnapshot([SESSION], [], [])
    const firstSessions = useSessionsStore.getState().sessions
    const firstUpdatedAt = useSessionsStore.getState().lastUpdatedAt
    useSessionsStore.getState().setSnapshot([{ ...SESSION }], [], [])
    expect(useSessionsStore.getState().sessions).toBe(firstSessions)
    expect(useSessionsStore.getState().lastUpdatedAt).toBe(firstUpdatedAt)
  })

  it('a real change swaps the reference', () => {
    useSessionsStore.getState().setSnapshot([SESSION], [], [])
    const first = useSessionsStore.getState().sessions
    useSessionsStore.getState().setSnapshot([{ ...SESSION, status: 'idle' }], [], [])
    expect(useSessionsStore.getState().sessions).not.toBe(first)
  })
})
