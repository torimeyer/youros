import { create } from 'zustand'

export interface LockSnapshot {
  name: string
  holder?: string
  created_at?: string
}

interface LocksState {
  locks: LockSnapshot[]
  wsConnected: boolean
  setLocks: (locks: LockSnapshot[]) => void
  setWsConnected: (connected: boolean) => void
}

export const useLocksStore = create<LocksState>((set) => ({
  locks: [],
  wsConnected: false,
  setLocks: (locks) => set({ locks }),
  setWsConnected: (wsConnected) => set({ wsConnected }),
}))
