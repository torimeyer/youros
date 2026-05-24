import { describe, it, expect, beforeEach } from 'vitest'
import { useDashboardStore } from './dashboardStore'

describe('useDashboardStore (→1131)', () => {
  beforeEach(() => {
    useDashboardStore.setState({ agentsCount: 0, tasksCount: 0, wsConnected: false, lastSyncAt: null })
  })

  it('setAgentsCount updates agentsCount', () => {
    useDashboardStore.getState().setAgentsCount(5)
    expect(useDashboardStore.getState().agentsCount).toBe(5)
  })

  it('setAgentsCount with zero clears count', () => {
    useDashboardStore.getState().setAgentsCount(3)
    useDashboardStore.getState().setAgentsCount(0)
    expect(useDashboardStore.getState().agentsCount).toBe(0)
  })

  it('setTasksCount updates tasksCount', () => {
    useDashboardStore.getState().setTasksCount(7)
    expect(useDashboardStore.getState().tasksCount).toBe(7)
  })

  it('setTasksCount with zero clears count', () => {
    useDashboardStore.getState().setTasksCount(4)
    useDashboardStore.getState().setTasksCount(0)
    expect(useDashboardStore.getState().tasksCount).toBe(0)
  })

  it('setWsConnected flips wsConnected flag', () => {
    expect(useDashboardStore.getState().wsConnected).toBe(false)
    useDashboardStore.getState().setWsConnected(true)
    expect(useDashboardStore.getState().wsConnected).toBe(true)
    useDashboardStore.getState().setWsConnected(false)
    expect(useDashboardStore.getState().wsConnected).toBe(false)
  })

  it('subsequent setAgentsCount overwrites previous state', () => {
    useDashboardStore.getState().setAgentsCount(2)
    useDashboardStore.getState().setAgentsCount(9)
    expect(useDashboardStore.getState().agentsCount).toBe(9)
  })

  it('initial state has zero counts and disconnected', () => {
    const state = useDashboardStore.getState()
    expect(state.agentsCount).toBe(0)
    expect(state.tasksCount).toBe(0)
    expect(state.wsConnected).toBe(false)
  })
})
