import { create } from 'zustand'
import { api } from '../lib/api'

export type AccentColor = 'blue' | 'pink' | 'purple' | 'cyan' | 'orange'

// Default order and visibility for home dashboard widgets. Users can
// hide any of these and reorder them via the Customize modal.
export const DEFAULT_DASHBOARD_WIDGETS: string[] = [
  'briefing',
  'focus_first',
  'todays_focus',
  'quick_launch',
  'next_meeting',
  'day_summary',
]

// Human readable labels for each dashboard widget id. Keep this in sync
// with DEFAULT_DASHBOARD_WIDGETS. Used by the customize modal.
export const DASHBOARD_WIDGET_LABELS: Record<string, string> = {
  briefing: 'Briefing',
  focus_first: 'Focus on this first',
  todays_focus: "Today's Focus",
  quick_launch: 'Quick Launch',
  next_meeting: 'Next Meeting',
  day_summary: 'Day Summary',
}

export interface FeatureToggle {
  label: string
  enabled: boolean
}

export interface CustomAgentTemplate {
  name: string
  description: string
  icon: string
  model: string
  budget: number
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
  tourComplete: boolean
  setTourComplete: (v: boolean) => void
  powerUserMode: boolean
  setPowerUserMode: (v: boolean) => void
  whatsNewLastSeen: string
  setWhatsNewLastSeen: (v: string) => void
  customAgentTemplates: CustomAgentTemplate[]
  setCustomAgentTemplates: (templates: CustomAgentTemplate[]) => void
  dashboardWidgets: string[]
  setDashboardWidgets: (widgets: string[]) => void
  hydrateFromServer: () => Promise<void>
}

// Keys used to cache user state in localStorage for fast first paint.
// These are a cache only. The server is the source of truth.
const LS_KEYS = {
  onboarded: 'myos-onboarded',
  darkMode: 'myos-dark-mode',
  accentColor: 'myos-accent-color',
  osName: 'myos-os-name',
  defaultChatModel: 'myos-default-chat-model',
  useOstkTerms: 'myos-use-ostk-terms',
  tourComplete: 'myos-tour-complete',
  powerUserMode: 'myos-power-user-mode',
  whatsNewLastSeen: 'myos-whats-new-last-seen',
  customAgentTemplates: 'myos-custom-templates',
  dashboardWidgets: 'myos-dashboard-widgets',
  chatWidth: 'myos-chat-width',
} as const

// Chat panel resize bounds.
// MIN keeps the chat usable (message list still readable, input still wide
// enough to type a sentence). MAX reserves room for the sidebar (224px)
// plus a sliver of main content so nothing gets hidden behind the chat.
export const CHAT_WIDTH_MIN = 280
export const CHAT_WIDTH_RESERVED_FOR_REST = 320

export function clampChatWidth(width: number, viewportWidth: number): number {
  const ceiling = Math.max(CHAT_WIDTH_MIN, viewportWidth - CHAT_WIDTH_RESERVED_FOR_REST)
  return Math.max(CHAT_WIDTH_MIN, Math.min(ceiling, Math.round(width)))
}

// Safe localStorage wrappers. Guard against non-browser environments (tests).
function lsGet(key: string): string | null {
  try {
    if (typeof localStorage === 'undefined') return null
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function lsSet(key: string, value: string): void {
  try {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(key, value)
  } catch {
    // ignore quota or access errors
  }
}

// Fire and forget server patch. We never want to throw from a setter.
function patchServer(body: Record<string, unknown>): void {
  api.patch('/settings', body).catch(() => {})
}

// Translate between UI model key ("claude") and server model string ("@claude").
function modelKeyToServer(key: string): string {
  return key.startsWith('@') ? key : `@${key}`
}
function serverModelToKey(server: string): string {
  return server.startsWith('@') ? server.slice(1) : server
}

// Read the persisted chat width. If nothing is stored, default to about
// a third of the current viewport so the chat feels right on first open.
// We still clamp to the live bounds so a stale localStorage value from an
// older device cannot leave the chat larger than today's viewport.
function readInitialChatWidth(): number {
  const viewport = typeof window !== 'undefined' ? window.innerWidth : 1200
  const raw = lsGet(LS_KEYS.chatWidth)
  if (raw) {
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && parsed > 0) {
      return clampChatWidth(parsed, viewport)
    }
  }
  return clampChatWidth(Math.floor(viewport / 3), viewport)
}

const initialOnboarded = lsGet(LS_KEYS.onboarded) === 'true'
const initialDarkMode = lsGet(LS_KEYS.darkMode) !== 'false'
const initialAccentColor = (lsGet(LS_KEYS.accentColor) as AccentColor) || 'blue'
const initialOsName = lsGet(LS_KEYS.osName) || 'myOS'
const initialDefaultChatModel = lsGet(LS_KEYS.defaultChatModel) || 'claude'
const initialUseOstkTerms = lsGet(LS_KEYS.useOstkTerms) === 'true'
const initialTourComplete = lsGet(LS_KEYS.tourComplete) === 'true'
const initialWhatsNewLastSeen = lsGet(LS_KEYS.whatsNewLastSeen) || ''

function readInitialCustomTemplates(): CustomAgentTemplate[] {
  const raw = lsGet(LS_KEYS.customAgentTemplates)
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}
const initialCustomAgentTemplates = readInitialCustomTemplates()

// One-shot migration: the old widget id "morning_briefing" became
// "briefing" when the time-of-day restriction was removed. Rename it
// wherever it appears in the stored widget list so users who already
// customized their dashboard do not lose the briefing tile.
function migrateBriefingWidgetId(ids: string[]): string[] {
  const out: string[] = []
  for (const id of ids) {
    const mapped = id === 'morning_briefing' ? 'briefing' : id
    if (!out.includes(mapped)) out.push(mapped)
  }
  return out
}

function readInitialDashboardWidgets(): string[] {
  const raw = lsGet(LS_KEYS.dashboardWidgets)
  if (!raw) return [...DEFAULT_DASHBOARD_WIDGETS]
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')) {
      return migrateBriefingWidgetId(parsed as string[])
    }
    return [...DEFAULT_DASHBOARD_WIDGETS]
  } catch {
    return [...DEFAULT_DASHBOARD_WIDGETS]
  }
}
const initialDashboardWidgets = readInitialDashboardWidgets()

export const useAppStore = create<AppState>((set, get) => ({
  onboarded: initialOnboarded,
  setOnboarded: (onboarded) => {
    lsSet(LS_KEYS.onboarded, String(onboarded))
    set({ onboarded })
    patchServer({ onboarded })
  },
  chatOpen: true,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  chatWidth: readInitialChatWidth(),
  setChatWidth: (chatWidth) => {
    const viewport = typeof window !== 'undefined' ? window.innerWidth : 1200
    const clamped = clampChatWidth(chatWidth, viewport)
    lsSet(LS_KEYS.chatWidth, String(clamped))
    set({ chatWidth: clamped })
  },
  isResizing: false,
  setIsResizing: (isResizing) => set({ isResizing }),
  osName: initialOsName,
  setOsName: (osName) => {
    lsSet(LS_KEYS.osName, osName)
    set({ osName })
    patchServer({ os_name: osName })
  },
  darkMode: initialDarkMode,
  toggleDarkMode: () => {
    const next = !get().darkMode
    lsSet(LS_KEYS.darkMode, String(next))
    set({ darkMode: next })
    patchServer({ dark_mode: next })
  },
  accentColor: initialAccentColor,
  setAccentColor: (accentColor) => {
    lsSet(LS_KEYS.accentColor, accentColor)
    set({ accentColor })
    patchServer({ accent_color: accentColor })
  },
  features: [
    { label: 'Chat', enabled: true },
    { label: 'Tasks', enabled: true },
    { label: 'Activity', enabled: true },
    { label: 'Hay/Ideas', enabled: true },
    { label: 'Agents', enabled: true },
    { label: 'Projects', enabled: true },
    { label: 'Drive', enabled: true },
    { label: 'Calendar', enabled: true },
    { label: 'Gmail', enabled: true },
    { label: 'Docs', enabled: true },
    { label: 'Transcripts', enabled: true },
    { label: 'Automations', enabled: true },
  ],
  setFeatures: (features) => set({ features }),
  isFeatureEnabled: (label: string) => {
    const feature = get().features.find((f) => f.label === label)
    return feature ? feature.enabled : true
  },
  defaultChatModel: initialDefaultChatModel,
  setDefaultChatModel: (defaultChatModel) => {
    lsSet(LS_KEYS.defaultChatModel, defaultChatModel)
    set({ defaultChatModel })
    patchServer({ default_model: modelKeyToServer(defaultChatModel) })
  },
  commandPaletteOpen: false,
  setCommandPaletteOpen: (commandPaletteOpen) => set({ commandPaletteOpen }),
  toggleCommandPalette: () => set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
  showTour: false,
  setShowTour: (showTour) => set({ showTour }),
  useOstkTerms: initialUseOstkTerms,
  setUseOstkTerms: (useOstkTerms) => {
    lsSet(LS_KEYS.useOstkTerms, String(useOstkTerms))
    set({ useOstkTerms })
    patchServer({ use_ostk_terms: useOstkTerms })
  },
  tourComplete: initialTourComplete,
  setTourComplete: (tourComplete) => {
    lsSet(LS_KEYS.tourComplete, String(tourComplete))
    set({ tourComplete })
    patchServer({ tour_complete: tourComplete })
  },
  powerUserMode: lsGet(LS_KEYS.powerUserMode) === 'true',
  setPowerUserMode: (powerUserMode) => {
    lsSet(LS_KEYS.powerUserMode, String(powerUserMode))
    set({ powerUserMode })
    patchServer({ power_user_mode: powerUserMode })
  },
  whatsNewLastSeen: initialWhatsNewLastSeen,
  setWhatsNewLastSeen: (whatsNewLastSeen) => {
    lsSet(LS_KEYS.whatsNewLastSeen, whatsNewLastSeen)
    set({ whatsNewLastSeen })
    patchServer({ whats_new_last_seen: whatsNewLastSeen })
  },
  customAgentTemplates: initialCustomAgentTemplates,
  setCustomAgentTemplates: (customAgentTemplates) => {
    lsSet(LS_KEYS.customAgentTemplates, JSON.stringify(customAgentTemplates))
    set({ customAgentTemplates })
    patchServer({ custom_agent_templates: customAgentTemplates })
  },
  dashboardWidgets: initialDashboardWidgets,
  setDashboardWidgets: (dashboardWidgets) => {
    lsSet(LS_KEYS.dashboardWidgets, JSON.stringify(dashboardWidgets))
    set({ dashboardWidgets })
    patchServer({ dashboard_widgets: dashboardWidgets })
  },
  hydrateFromServer: async () => {
    let server: Record<string, unknown> = {}
    try {
      server = await api.get<Record<string, unknown>>('/settings')
    } catch {
      return
    }

    // For each field we care about: if the server has a value, it wins.
    // If the server is silent (null or undefined), keep the current local
    // value and write it back to the server so future loads have it. This
    // is a one time migration from localStorage to server storage.
    const state = get()
    const updates: Partial<AppState> = {}
    const backfill: Record<string, unknown> = {}

    const hasValue = (v: unknown) => v !== undefined && v !== null

    // onboarded
    if (hasValue(server.onboarded)) {
      const v = Boolean(server.onboarded)
      updates.onboarded = v
      lsSet(LS_KEYS.onboarded, String(v))
    } else if (state.onboarded) {
      backfill.onboarded = state.onboarded
    }

    // os_name
    if (hasValue(server.os_name)) {
      const v = String(server.os_name)
      updates.osName = v
      lsSet(LS_KEYS.osName, v)
    } else if (state.osName && state.osName !== 'myOS') {
      backfill.os_name = state.osName
    }

    // dark_mode
    if (hasValue(server.dark_mode)) {
      const v = Boolean(server.dark_mode)
      updates.darkMode = v
      lsSet(LS_KEYS.darkMode, String(v))
    } else {
      backfill.dark_mode = state.darkMode
    }

    // accent_color
    if (hasValue(server.accent_color)) {
      const v = server.accent_color as AccentColor
      updates.accentColor = v
      lsSet(LS_KEYS.accentColor, v)
    } else if (state.accentColor && state.accentColor !== 'blue') {
      backfill.accent_color = state.accentColor
    }

    // default_model (server uses "@claude" form, store uses "claude")
    if (hasValue(server.default_model)) {
      const v = serverModelToKey(String(server.default_model))
      updates.defaultChatModel = v
      lsSet(LS_KEYS.defaultChatModel, v)
    } else if (state.defaultChatModel && state.defaultChatModel !== 'claude') {
      backfill.default_model = modelKeyToServer(state.defaultChatModel)
    }

    // use_ostk_terms
    if (hasValue(server.use_ostk_terms)) {
      const v = Boolean(server.use_ostk_terms)
      updates.useOstkTerms = v
      lsSet(LS_KEYS.useOstkTerms, String(v))
    } else if (state.useOstkTerms) {
      backfill.use_ostk_terms = state.useOstkTerms
    }

    // tour_complete
    if (hasValue(server.tour_complete)) {
      const v = Boolean(server.tour_complete)
      updates.tourComplete = v
      lsSet(LS_KEYS.tourComplete, String(v))
    } else if (state.tourComplete) {
      backfill.tour_complete = state.tourComplete
    }

    // power_user_mode
    if (hasValue(server.power_user_mode)) {
      const v = Boolean(server.power_user_mode)
      updates.powerUserMode = v
      lsSet(LS_KEYS.powerUserMode, String(v))
    } else if (state.powerUserMode) {
      backfill.power_user_mode = state.powerUserMode
    }

    // whats_new_last_seen
    if (hasValue(server.whats_new_last_seen)) {
      const v = String(server.whats_new_last_seen)
      updates.whatsNewLastSeen = v
      lsSet(LS_KEYS.whatsNewLastSeen, v)
    } else if (state.whatsNewLastSeen) {
      backfill.whats_new_last_seen = state.whatsNewLastSeen
    }

    // custom_agent_templates
    if (hasValue(server.custom_agent_templates) && Array.isArray(server.custom_agent_templates)) {
      const v = server.custom_agent_templates as CustomAgentTemplate[]
      updates.customAgentTemplates = v
      lsSet(LS_KEYS.customAgentTemplates, JSON.stringify(v))
    } else if (state.customAgentTemplates && state.customAgentTemplates.length > 0) {
      backfill.custom_agent_templates = state.customAgentTemplates
    }

    // dashboard_widgets
    if (
      hasValue(server.dashboard_widgets) &&
      Array.isArray(server.dashboard_widgets) &&
      (server.dashboard_widgets as unknown[]).every((x) => typeof x === 'string')
    ) {
      const v = migrateBriefingWidgetId(server.dashboard_widgets as string[])
      updates.dashboardWidgets = v
      lsSet(LS_KEYS.dashboardWidgets, JSON.stringify(v))
    } else {
      backfill.dashboard_widgets = state.dashboardWidgets
    }

    if (Object.keys(updates).length > 0) {
      set(updates)
    }
    if (Object.keys(backfill).length > 0) {
      patchServer(backfill)
    }
  },
}))
