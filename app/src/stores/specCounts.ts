import { create } from 'zustand'

interface SpecCountsState {
  unfinished: number
  total: number
  connected: boolean
  lastUpdatedAt: string | null
  setSnapshot: (unfinished: number, total: number) => void
  setConnected: (connected: boolean) => void
}

export const useSpecCountsStore = create<SpecCountsState>((set) => ({
  unfinished: 0,
  total: 0,
  connected: false,
  lastUpdatedAt: null,
  setSnapshot: (unfinished, total) =>
    set({ unfinished, total, lastUpdatedAt: new Date().toISOString() }),
  setConnected: (connected) => set({ connected }),
}))
