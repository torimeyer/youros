import { create } from 'zustand'

interface DashboardState {
  agentsCount: number
  tasksCount: number
  wsConnected: boolean
  lastSyncAt: string | null
  setAgentsCount: (count: number) => void
  setTasksCount: (count: number) => void
  setWsConnected: (connected: boolean) => void
}

export const useDashboardStore = create<DashboardState>((set) => ({
  agentsCount: 0,
  tasksCount: 0,
  wsConnected: false,
  lastSyncAt: null,
  setAgentsCount: (count) => set({ agentsCount: count }),
  setTasksCount: (count) => set({ tasksCount: count }),
  setWsConnected: (connected) => set({ wsConnected: connected }),
}))
