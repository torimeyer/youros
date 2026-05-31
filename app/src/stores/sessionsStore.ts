import { create } from 'zustand'
import { shallowEqualArray } from './storeEquality'

export interface SessionRow {
  id: string
  name: string
  type: string
  started_at: string | null
  last_active_at: string
  status: 'active' | 'idle'
}

export interface LockRow {
  lock_name: string
  held_by_session: string
  started_at: string | null
  paths: string[]
}

export interface EventRow {
  from_session: string
  to_session: string
  message: string
  timestamp: string
  kind: string
}

interface SessionsState {
  sessions: SessionRow[]
  locks: LockRow[]
  events: EventRow[]
  connected: boolean
  lastUpdatedAt: string | null
  setSnapshot: (sessions: SessionRow[], locks: LockRow[], events: EventRow[]) => void
  setConnected: (connected: boolean) => void
}

export const useSessionsStore = create<SessionsState>((set) => ({
  sessions: [],
  locks: [],
  events: [],
  connected: false,
  lastUpdatedAt: null,
  setSnapshot: (sessions, locks, events) =>
    set((prev) =>
      shallowEqualArray(prev.sessions, sessions) &&
      shallowEqualArray(prev.locks, locks) &&
      shallowEqualArray(prev.events, events)
        ? prev
        : { sessions, locks, events, lastUpdatedAt: new Date().toISOString() },
    ),
  setConnected: (connected) => set({ connected }),
}))
