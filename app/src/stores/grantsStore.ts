import { create } from 'zustand'

export interface GrantRequest {
  id: string
  agent: string
  type: string
  target: string
  status: string
  requested_at: string
  detail?: string
}

interface GrantsState {
  grants: GrantRequest[]
  connected: boolean
  setGrants: (grants: GrantRequest[]) => void
  setConnected: (connected: boolean) => void
}

export const useGrantsStore = create<GrantsState>((set) => ({
  grants: [],
  connected: false,
  setGrants: (grants) => set({ grants }),
  setConnected: (connected) => set({ connected }),
}))
