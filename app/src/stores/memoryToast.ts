import { create } from 'zustand'

export type ToastMode = 'added' | 'forgot' | 'ambiguous' | 'failed' | null

interface MemoryToastState {
  mode: ToastMode
  /** Bullet text — used in 'added' and 'forgot' modes. */
  bullet: string | null
  /** Section the forgotten bullet came from — used for Undo restore. */
  section: string
  /** Candidate bullets — used in 'ambiguous' mode. */
  candidates: string[]

  trigger: (text: string) => void
  triggerForgot: (bullet: string, section: string) => void
  triggerAmbiguous: (candidates: string[]) => void
  triggerFailed: () => void
  clear: () => void
}

export const useMemoryToastStore = create<MemoryToastState>((set) => ({
  mode: null,
  bullet: null,
  section: 'Preferences',
  candidates: [],

  trigger: (text) => set({ mode: 'added', bullet: text }),
  triggerForgot: (bullet, section) => set({ mode: 'forgot', bullet, section }),
  triggerAmbiguous: (candidates) => set({ mode: 'ambiguous', candidates }),
  triggerFailed: () => set({ mode: 'failed' }),
  clear: () => set({ mode: null, bullet: null, candidates: [] }),
}))
