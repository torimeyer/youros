import { create } from 'zustand'

interface DashboardState {
  agents_count: number
  tasks_count: number
  ws_connected: boolean
  setAgentsCount: (count: number) => void
  setTasksCount: (count: number) => void
  setWsConnected: (connected: boolean) => void
}

export const useDashboardStore = create<DashboardState>((set) => ({
  agents_count: 0,
  tasks_count: 0,
  ws_connected: false,
  setAgentsCount: (agents_count) => set({ agents_count }),
  setTasksCount: (tasks_count) => set({ tasks_count }),
  setWsConnected: (ws_connected) => set({ ws_connected }),
}))
