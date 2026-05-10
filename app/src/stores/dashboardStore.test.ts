import { describe, it, expect, beforeEach } from 'vitest'
import { useDashboardStore } from './dashboardStore'

describe('useDashboardStore (→1131)', () => {
  beforeEach(() => {
    useDashboardStore.setState({ agents_count: 0, tasks_count: 0, ws_connected: false })
  })

  it('setAgentsCount updates agents_count', () => {
    useDashboardStore.getState().setAgentsCount(5)
    expect(useDashboardStore.getState().agents_count).toBe(5)
  })

  it('setAgentsCount with zero clears count', () => {
    useDashboardStore.getState().setAgentsCount(3)
    useDashboardStore.getState().setAgentsCount(0)
    expect(useDashboardStore.getState().agents_count).toBe(0)
  })

  it('setTasksCount updates tasks_count', () => {
    useDashboardStore.getState().setTasksCount(7)
    expect(useDashboardStore.getState().tasks_count).toBe(7)
  })

  it('setTasksCount with zero clears count', () => {
    useDashboardStore.getState().setTasksCount(4)
    useDashboardStore.getState().setTasksCount(0)
    expect(useDashboardStore.getState().tasks_count).toBe(0)
  })

  it('setWsConnected flips ws_connected flag', () => {
    expect(useDashboardStore.getState().ws_connected).toBe(false)
    useDashboardStore.getState().setWsConnected(true)
    expect(useDashboardStore.getState().ws_connected).toBe(true)
    useDashboardStore.getState().setWsConnected(false)
    expect(useDashboardStore.getState().ws_connected).toBe(false)
  })

  it('subsequent setAgentsCount overwrites previous state', () => {
    useDashboardStore.getState().setAgentsCount(2)
    useDashboardStore.getState().setAgentsCount(9)
    expect(useDashboardStore.getState().agents_count).toBe(9)
  })

  it('initial state has zero counts and disconnected', () => {
    const state = useDashboardStore.getState()
    expect(state.agents_count).toBe(0)
    expect(state.tasks_count).toBe(0)
    expect(state.ws_connected).toBe(false)
  })
})
