import { create } from 'zustand'

export type AccentColor = 'blue' | 'pink' | 'purple' | 'cyan' | 'orange'

export interface FeatureToggle {
  label: string
  enabled: boolean
}

// Maps Settings provider names to chat model keys
export const PROVIDER_TO_MODEL: Record<string, string> = {
  'Anthropic': 'claude',
  'Google Gemini': 'gemini',
}

// Terminology mapping: ostk terms vs plain language
const OSTK_TERMS = {
  task: 'Needle', tasks: 'Needles',
  idea: 'Hay', ideas: 'Hay',
  note: 'Straw', notes: 'Straws',
} as const

const STANDARD_TERMS = {
  task: 'Task', tasks: 'Tasks',
  idea: 'Idea', ideas: 'Ideas',
  note: 'Note', notes: 'Notes',
} as const

export type TermKey = keyof typeof STANDARD_TERMS

export function useTerms() {
  const ostkTerms = useAppStore((s) => s.useOstkTerms)
  const map = ostkTerms ? OSTK_TERMS : STANDARD_TERMS
  return (key: TermKey) => map[key]
}

interface AppState {
  onboarded: boolean
  setOnboarded: (v: boolean) => void
  chatOpen: boolean
  toggleChat: () => void
  chatWidth: number
  setChatWidth: (w: number) => void
  isResizing: boolean
  setIsResizing: (v: boolean) => void
  osName: string
  setOsName: (name: string) => void
  darkMode: boolean
  toggleDarkMode: () => void
  accentColor: AccentColor
  setAccentColor: (color: AccentColor) => void
  features: FeatureToggle[]
  setFeatures: (features: FeatureToggle[]) => void
  isFeatureEnabled: (label: string) => boolean
  defaultChatModel: string
  setDefaultChatModel: (model: string) => void
  commandPaletteOpen: boolean
  setCommandPaletteOpen: (open: boolean) => void
  toggleCommandPalette: () => void
  showTour: boolean
  setShowTour: (show: boolean) => void
  useOstkTerms: boolean
  setUseOstkTerms: (v: boolean) => void
}

export const useAppStore = create<AppState>((set, get) => ({
  onboarded: localStorage.getItem('myos-onboarded') === 'true',
  setOnboarded: (onboarded) => {
    localStorage.setItem('myos-onboarded', String(onboarded))
    set({ onboarded })
  },
  chatOpen: true,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  chatWidth: 380,
  setChatWidth: (chatWidth) => set({ chatWidth: Math.max(300, Math.min(Math.floor(window.innerWidth / 2), chatWidth)) }),
  isResizing: false,
  setIsResizing: (isResizing) => set({ isResizing }),
  osName: 'myOS',
  setOsName: (osName) => set({ osName }),
  darkMode: localStorage.getItem('myos-dark-mode') !== 'false',
  toggleDarkMode: () => set((s) => {
    const next = !s.darkMode
    localStorage.setItem('myos-dark-mode', String(next))
    return { darkMode: next }
  }),
  accentColor: (localStorage.getItem('myos-accent-color') as AccentColor) || 'blue',
  setAccentColor: (accentColor) => {
    localStorage.setItem('myos-accent-color', accentColor)
    set({ accentColor })
  },
  features: [
    { label: 'Chat', enabled: true },
    { label: 'Tasks', enabled: true },
    { label: 'Hay/Ideas', enabled: true },
    { label: 'Agents', enabled: true },
    { label: 'Projects', enabled: true },
    { label: 'Docs', enabled: true },
    { label: 'Transcripts', enabled: false },
  ],
  setFeatures: (features) => set({ features }),
  isFeatureEnabled: (label: string) => {
    const feature = get().features.find((f) => f.label === label)
    return feature ? feature.enabled : true
  },
  defaultChatModel: 'claude',
  setDefaultChatModel: (defaultChatModel) => set({ defaultChatModel }),
  commandPaletteOpen: false,
  setCommandPaletteOpen: (commandPaletteOpen) => set({ commandPaletteOpen }),
  toggleCommandPalette: () => set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
  showTour: false,
  setShowTour: (showTour) => set({ showTour }),
  useOstkTerms: false,
  setUseOstkTerms: (useOstkTerms) => set({ useOstkTerms }),
}))
