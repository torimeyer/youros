import { describe, it, expect, beforeEach } from 'vitest'
import { useRunningAgentsStore } from './runningAgents'

describe('useRunningAgentsStore', () => {
  beforeEach(() => {
    useRunningAgentsStore.setState({
      count: 0,
      agents: [],
      connected: false,
      lastUpdatedAt: null,
    })
  })

  it('setSnapshot updates count and agents', () => {
    const { setSnapshot } = useRunningAgentsStore.getState()
    setSnapshot(3, [
      { name: 'agent-a', status: 'running' },
      { name: 'agent-b', status: 'spawned' },
      { name: 'agent-c', status: 'starting' },
    ])
    const s = useRunningAgentsStore.getState()
    expect(s.count).toBe(3)
    expect(s.agents).toHaveLength(3)
    expect(s.lastUpdatedAt).not.toBeNull()
  })

  it('setSnapshot with empty list sets count to 0', () => {
    const { setSnapshot } = useRunningAgentsStore.getState()
    setSnapshot(0, [])
    expect(useRunningAgentsStore.getState().count).toBe(0)
    expect(useRunningAgentsStore.getState().agents).toHaveLength(0)
  })

  it('setConnected flips connected flag', () => {
    const { setConnected } = useRunningAgentsStore.getState()
    expect(useRunningAgentsStore.getState().connected).toBe(false)
    setConnected(true)
    expect(useRunningAgentsStore.getState().connected).toBe(true)
    setConnected(false)
    expect(useRunningAgentsStore.getState().connected).toBe(false)
  })

  it('subsequent setSnapshot overwrites previous state', () => {
    const { setSnapshot } = useRunningAgentsStore.getState()
    setSnapshot(2, [{ name: 'a', status: 'running' }, { name: 'b', status: 'running' }])
    setSnapshot(1, [{ name: 'a', status: 'running' }])
    const s = useRunningAgentsStore.getState()
    expect(s.count).toBe(1)
    expect(s.agents).toHaveLength(1)
  })
})
