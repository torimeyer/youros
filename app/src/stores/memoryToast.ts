import { create } from 'zustand'

interface MemoryToastState {
  /** Currently visible bullet text, or null when no toast is showing. */
  bullet: string | null
  trigger: (text: string) => void
  clear: () => void
}

export const useMemoryToastStore = create<MemoryToastState>((set) => ({
  bullet: null,
  trigger: (text) => set({ bullet: text }),
  clear: () => set({ bullet: null }),
}))
