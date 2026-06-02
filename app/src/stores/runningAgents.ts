import { create } from 'zustand'
import { shallowEqualArray } from './storeEquality'

export interface RunningAgent {
  name: string
  status: string
  task_id?: string | null
  needle_id?: string | null
  label?: string | null
  build_state?: 'running' | 'queued' | null
}

export interface TerminatedAgent {
  name: string
  status: string
  feedback: string
}

interface RunningAgentsState {
  count: number
  agents: RunningAgent[]
  connected: boolean
  lastUpdatedAt: string | null
  lastTerminatedAgent: TerminatedAgent | null
  setSnapshot: (count: number, agents: RunningAgent[]) => void
  setConnected: (connected: boolean) => void
  setTerminatedAgent: (agent: TerminatedAgent) => void
}

export const useRunningAgentsStore = create<RunningAgentsState>((set) => ({
  count: 0,
  agents: [],
  connected: false,
  lastUpdatedAt: null,
  lastTerminatedAgent: null,
  setSnapshot: (count, agents) =>
    // Preserve the previous state (and the previous `agents` reference) when
    // the incoming snapshot is content-equal, so no subscriber re-renders for
    // a no-op WS frame. See storeEquality.ts (→1730).
    set((prev) =>
      prev.count === count && shallowEqualArray(prev.agents, agents)
        ? prev
        : { count, agents, lastUpdatedAt: new Date().toISOString() },
    ),
  setConnected: (connected) => set({ connected }),
  setTerminatedAgent: (agent) => set({ lastTerminatedAgent: agent }),
}))
