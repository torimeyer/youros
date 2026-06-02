import { create } from 'zustand'
import { shallowEqualArray } from './storeEquality'

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
  setLocks: (locks) =>
    set((prev) => (shallowEqualArray(prev.locks, locks) ? prev : { locks })),
  setWsConnected: (wsConnected) => set({ wsConnected }),
}))
