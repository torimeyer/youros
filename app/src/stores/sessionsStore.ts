import { create } from 'zustand'

export interface SessionSnapshot {
  session_id: string
  last_active: string
  status: 'active' | 'idle'
  recent_events?: Array<{ type: string; timestamp: string }>
}

interface SessionsState {
  count: number
  sessions: SessionSnapshot[]
  connected: boolean
  setSnapshot: (count: number, sessions: SessionSnapshot[]) => void
  setConnected: (connected: boolean) => void
}

export const useSessionsStore = create<SessionsState>((set) => ({
  count: 0,
  sessions: [],
  connected: false,
  setSnapshot: (count, sessions) => set({ count, sessions }),
  setConnected: (connected) => set({ connected }),
}))
