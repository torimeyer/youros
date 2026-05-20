import { create } from 'zustand'

interface MemoryToastState {
  /** Currently visible bullet text for "Got it" toast, or null. */
  bullet: string | null
  /** Removed bullet text for "Forgot" toast, or null. */
  forgot: string | null
  /** Multiple candidates returned when forget phrase is ambiguous. */
  forgotCandidates: string[] | null
  trigger: (text: string) => void
  triggerForgot: (text: string) => void
  triggerForgotAmbiguous: (candidates: string[]) => void
  clear: () => void
  clearForgot: () => void
}

export const useMemoryToastStore = create<MemoryToastState>((set) => ({
  bullet: null,
  forgot: null,
  forgotCandidates: null,
  trigger: (text) => set({ bullet: text }),
  triggerForgot: (text) => set({ forgot: text, forgotCandidates: null }),
  triggerForgotAmbiguous: (candidates) => set({ forgotCandidates: candidates, forgot: null }),
  clear: () => set({ bullet: null }),
  clearForgot: () => set({ forgot: null, forgotCandidates: null }),
}))
