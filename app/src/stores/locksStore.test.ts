import { describe, it, expect, beforeEach } from 'vitest'
import { useLocksStore } from './locksStore'

describe('useLocksStore', () => {
  beforeEach(() => {
    useLocksStore.setState({ locks: [], wsConnected: false })
  })

  it('initial state has no locks and wsConnected=false', () => {
    const s = useLocksStore.getState()
    expect(s.locks).toEqual([])
    expect(s.wsConnected).toBe(false)
  })

  it('setLocks updates the locks list', () => {
    const { setLocks } = useLocksStore.getState()
    setLocks([
      { name: 'task-lock', holder: 'agent-abc' },
      { name: 'deploy-lock' },
    ])
    const s = useLocksStore.getState()
    expect(s.locks).toHaveLength(2)
    expect(s.locks[0].name).toBe('task-lock')
    expect(s.locks[0].holder).toBe('agent-abc')
    expect(s.locks[1].name).toBe('deploy-lock')
  })

  it('setLocks with empty array clears locks', () => {
    useLocksStore.setState({ locks: [{ name: 'old-lock' }] })
    useLocksStore.getState().setLocks([])
    expect(useLocksStore.getState().locks).toEqual([])
  })

  it('setWsConnected updates wsConnected flag', () => {
    const { setWsConnected } = useLocksStore.getState()
    setWsConnected(true)
    expect(useLocksStore.getState().wsConnected).toBe(true)
    setWsConnected(false)
    expect(useLocksStore.getState().wsConnected).toBe(false)
  })

  it('setLocks does not mutate wsConnected', () => {
    useLocksStore.setState({ wsConnected: true })
    useLocksStore.getState().setLocks([{ name: 'lock-x' }])
    expect(useLocksStore.getState().wsConnected).toBe(true)
  })
})
