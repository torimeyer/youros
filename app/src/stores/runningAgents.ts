import { create } from 'zustand'

export interface RunningAgent {
  name: string
  status: string
  task_id?: string | null
  needle_id?: string | null
  label?: string | null
  build_state?: 'running' | 'queued' | null
}

interface RunningAgentsState {
  count: number
  agents: RunningAgent[]
  connected: boolean
  lastUpdatedAt: string | null
  openTaskCount: number
  setSnapshot: (count: number, agents: RunningAgent[], openTaskCount?: number) => void
  setConnected: (connected: boolean) => void
}

export const useRunningAgentsStore = create<RunningAgentsState>((set) => ({
  count: 0,
  agents: [],
  connected: false,
  lastUpdatedAt: null,
  openTaskCount: -1,
  setSnapshot: (count, agents, openTaskCount) =>
    set({
      count,
      agents,
      lastUpdatedAt: new Date().toISOString(),
      ...(openTaskCount !== undefined ? { openTaskCount } : {}),
    }),
  setConnected: (connected) => set({ connected }),
}))
