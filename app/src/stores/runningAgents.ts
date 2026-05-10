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
  setSnapshot: (count: number, agents: RunningAgent[]) => void
  setConnected: (connected: boolean) => void
}

export const useRunningAgentsStore = create<RunningAgentsState>((set) => ({
  count: 0,
  agents: [],
  connected: false,
  lastUpdatedAt: null,
  setSnapshot: (count, agents) =>
    set({ count, agents, lastUpdatedAt: new Date().toISOString() }),
  setConnected: (connected) => set({ connected }),
}))
