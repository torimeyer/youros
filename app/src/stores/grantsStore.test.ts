import { describe, it, expect, beforeEach } from 'vitest'
import { useGrantsStore } from './grantsStore'

const GRANT = {
  id: 'g-1',
  agent: 'my-agent',
  type: 'tool',
  target: 'bash',
  status: 'pending',
  requested_at: '2026-05-10T00:00:00Z',
}

describe('useGrantsStore (→1129)', () => {
  beforeEach(() => {
    useGrantsStore.setState({ grants: [], connected: false })
  })

  it('setGrants replaces grants list', () => {
    useGrantsStore.getState().setGrants([GRANT])
    expect(useGrantsStore.getState().grants).toHaveLength(1)
    expect(useGrantsStore.getState().grants[0].id).toBe('g-1')
  })

  it('setGrants with empty array clears grants', () => {
    useGrantsStore.getState().setGrants([GRANT])
    useGrantsStore.getState().setGrants([])
    expect(useGrantsStore.getState().grants).toHaveLength(0)
  })

  it('setConnected flips connected flag', () => {
    expect(useGrantsStore.getState().connected).toBe(false)
    useGrantsStore.getState().setConnected(true)
    expect(useGrantsStore.getState().connected).toBe(true)
    useGrantsStore.getState().setConnected(false)
    expect(useGrantsStore.getState().connected).toBe(false)
  })

  it('subsequent setGrants overwrites previous state', () => {
    useGrantsStore.getState().setGrants([GRANT])
    const grant2 = { ...GRANT, id: 'g-2', agent: 'other-agent' }
    useGrantsStore.getState().setGrants([grant2])
    const { grants } = useGrantsStore.getState()
    expect(grants).toHaveLength(1)
    expect(grants[0].id).toBe('g-2')
  })
})
