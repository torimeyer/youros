import { create } from 'zustand'
import { api } from '../lib/api'

export type AccentColor = 'blue' | 'pink' | 'purple' | 'cyan' | 'orange'

export const TEAM_MODE_VISIBLE = false

// Default order and visibility for home dashboard widgets. Users can
// hide any of these and reorder them via the Customize modal.
export const DEFAULT_DASHBOARD_WIDGETS: string[] = [
  'briefing',
  'focus_first',
  'adventure',
  'todays_focus',
  'quick_launch',
  'next_meeting',
  'day_summary',
  'recent_specs',
]

// Human readable labels for each dashboard widget id. Keep this in sync
// with DEFAULT_DASHBOARD_WIDGETS. Used by the customize modal.
export const DASHBOARD_WIDGET_LABELS: Record<string, string> = {
  briefing: 'Briefing',
  focus_first: 'Focus on this first',
  adventure: 'Try an Adventure',
  todays_focus: "Today's Focus",
  quick_launch: 'Quick Launch',
  next_meeting: 'Next Event',
  day_summary: 'Day Summary',
  recent_specs: 'Recent Specs',
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

// Standard terminology (always used)
const TERMS = {
  task: 'Task', tasks: 'Tasks',
  note: 'Note', notes: 'Notes',
} as const

export type TermKey = keyof typeof TERMS

export function useTerms() {
  return (key: TermKey) => TERMS[key]
}

export interface EnterpriseUser {
  authenticated: boolean
  email: string
  role: string
}

export type InstanceMode = 'personal' | 'team'

export type SidebarPosition = 'left' | 'right'
export type FontSize = 'small' | 'medium' | 'large'
export type IconStyle = 'filled' | 'outlined'
export type CardStyle = 'glass' | 'solid'
export type DashboardLayout = 'full' | 'focus'
export type StatusDotStyle = 'dots' | 'badges'
export type GreetingStyle = 'time' | 'quote' | 'none'

// A spec that the user just asked to create (via "Make spec" on a
// roadmap bullet) whose backend generation is still in flight. Lives
// in the store so the Specs page can render a skeleton row the instant
// the user navigates there, without waiting on the POST to return.
// Keyed by tempId in pendingSpecs so a single session can have several
// in flight at once.
export interface PendingSpec {
  tempId: string
  title: string
  status: 'generating' | 'ready' | 'error'
  errorMsg?: string
  promotedPath?: string | null
}

interface AppState {
  hydrated: boolean
  onboarded: boolean
  setOnboarded: (v: boolean) => void
  chatOpen: boolean
  toggleChat: () => void
  setChatOpen: (open: boolean) => void
  chatWidth: number
  setChatWidth: (w: number) => void
  isResizing: boolean
  setIsResizing: (v: boolean) => void
  osName: string
  setOsName: (name: string) => void
  // Per-instance name. myOS is the product. Every user picks what their
  // own copy is called. Default matches the product so a user who never
  // changes it still sees "myOS" in the UI. Tori's copy is "toriOS".
  instanceName: string
  setInstanceName: (name: string) => void
  darkMode: boolean
  toggleDarkMode: () => void
  accentColor: AccentColor
  setAccentColor: (color: AccentColor) => void
  features: FeatureToggle[]
  setFeatures: (features: FeatureToggle[]) => void
  isFeatureEnabled: (label: string) => boolean
  defaultChatModel: string
  setDefaultChatModel: (model: string) => void
  sideBySideEnabled: boolean
  setSideBySideEnabled: (v: boolean) => void
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
  agentsLastViewed: string
  setAgentsLastViewed: (v: string) => void
  customAgentTemplates: CustomAgentTemplate[]
  setCustomAgentTemplates: (templates: CustomAgentTemplate[]) => void
  dashboardWidgets: string[]
  setDashboardWidgets: (widgets: string[]) => void
  enterpriseUser: EnterpriseUser | null
  instanceMode: InstanceMode
  setInstanceMode: (mode: InstanceMode) => void
  orgName: string
  setOrgName: (name: string) => void
  teamAccentColor: string
  setTeamAccentColor: (color: string) => void
  displayOsName: () => string
  hydrateFromServer: () => Promise<void>
  // Appearance settings
  sidebarPosition: SidebarPosition
  setSidebarPosition: (v: SidebarPosition) => void
  compactMode: boolean
  setCompactMode: (v: boolean) => void
  fontSize: FontSize
  setFontSize: (v: FontSize) => void
  iconStyle: IconStyle
  setIconStyle: (v: IconStyle) => void
  cardStyle: CardStyle
  setCardStyle: (v: CardStyle) => void
  dashboardLayout: DashboardLayout
  setDashboardLayout: (v: DashboardLayout) => void
  statusDotStyle: StatusDotStyle
  setStatusDotStyle: (v: StatusDotStyle) => void
  greetingStyle: GreetingStyle
  setGreetingStyle: (v: GreetingStyle) => void
  showBudgetCaps: boolean
  setShowBudgetCaps: (v: boolean) => void
  // Optimistic pending-spec queue. FilePreviewPane pushes a row here
  // when the user clicks "Make spec" so the Specs page can render a
  // skeleton immediately instead of waiting on AC generation.
  pendingSpecs: Record<string, PendingSpec>
  addPendingSpec: (spec: PendingSpec) => void
  updatePendingSpec: (tempId: string, patch: Partial<PendingSpec>) => void
  removePendingSpec: (tempId: string) => void
}

// Keys used to cache user state in localStorage for fast first paint.
// These are a cache only. The server is the source of truth.
const LS_KEYS = {
  onboarded: 'myos-onboarded',
  darkMode: 'myos-dark-mode',
  accentColor: 'myos-accent-color',
  osName: 'myos-os-name',
  instanceName: 'myos-instance-name',
  defaultChatModel: 'myos-default-chat-model',
  sideBySideEnabled: 'myos-ephemeral-side-by-side-enabled',
  useOstkTerms: 'myos-use-ostk-terms',
  tourComplete: 'myos-tour-complete',
  powerUserMode: 'myos-power-user-mode',
  whatsNewLastSeen: 'myos-whats-new-last-seen',
  agentsLastViewed: 'myos-agents-last-viewed',
  customAgentTemplates: 'myos-custom-templates',
  dashboardWidgets: 'myos-dashboard-widgets',
  chatWidth: 'myos-chat-width',
  featureOrder: 'myos-feature-order',
  sidebarPosition: 'myos-sidebar-position',
  compactMode: 'myos-compact-mode',
  fontSize: 'myos-font-size',
  iconStyle: 'myos-icon-style',
  cardStyle: 'myos-card-style',
  dashboardLayout: 'myos-dashboard-layout',
  statusDotStyle: 'myos-status-dot-style',
  greetingStyle: 'myos-greeting-style',
  showBudgetCaps: 'myos-show-budget-caps',
  instanceMode: 'myos-instance-mode',
  orgName: 'myos-org-name',
  teamAccentColor: 'myos-team-accent-color',
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
  api.patch('/settings', body)?.catch((e) => console.error('settings patch failed:', e))
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
const initialInstanceName = lsGet(LS_KEYS.instanceName) || 'myOS'
const initialDefaultChatModel = lsGet(LS_KEYS.defaultChatModel) || 'claude'
const initialSideBySideEnabled = lsGet(LS_KEYS.sideBySideEnabled) === 'true'
const initialUseOstkTerms = lsGet(LS_KEYS.useOstkTerms) === 'true'
const initialTourComplete = lsGet(LS_KEYS.tourComplete) === 'true'
const initialWhatsNewLastSeen = lsGet(LS_KEYS.whatsNewLastSeen) || ''
const initialAgentsLastViewed = lsGet(LS_KEYS.agentsLastViewed) || ''
const initialSidebarPosition = (lsGet(LS_KEYS.sidebarPosition) as SidebarPosition) || 'left'
const initialCompactMode = lsGet(LS_KEYS.compactMode) === 'true'
const initialFontSize = (lsGet(LS_KEYS.fontSize) as FontSize) || 'medium'
const initialIconStyle = (lsGet(LS_KEYS.iconStyle) as IconStyle) || 'filled'
const initialCardStyle = (lsGet(LS_KEYS.cardStyle) as CardStyle) || 'glass'
const initialDashboardLayout = (lsGet(LS_KEYS.dashboardLayout) as DashboardLayout) || 'full'
const initialStatusDotStyle = (lsGet(LS_KEYS.statusDotStyle) as StatusDotStyle) || 'dots'
const initialGreetingStyle = (lsGet(LS_KEYS.greetingStyle) as GreetingStyle) || 'time'
const initialShowBudgetCaps = lsGet(LS_KEYS.showBudgetCaps) === 'true'
const initialInstanceMode = (lsGet(LS_KEYS.instanceMode) as InstanceMode) || 'personal'
const initialOrgName = lsGet(LS_KEYS.orgName) || ''
const initialTeamAccentColor = lsGet(LS_KEYS.teamAccentColor) || '#6366f1'

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

function applyFeatureOrder(features: FeatureToggle[]): FeatureToggle[] {
  const raw = lsGet(LS_KEYS.featureOrder)
  if (!raw) return features
  try {
    const order: string[] = JSON.parse(raw)
    if (!Array.isArray(order)) return features
    const byLabel = new Map(features.map((f) => [f.label, f]))
    const result: FeatureToggle[] = []
    for (const label of order) {
      const f = byLabel.get(label)
      if (f) {
        result.push(f)
        byLabel.delete(label)
      }
    }
    // Append any new features not in the saved order
    for (const f of byLabel.values()) result.push(f)
    return result
  } catch {
    return features
  }
}

export const useAppStore = create<AppState>((set, get) => ({
  hydrated: false,
  onboarded: initialOnboarded,
  setOnboarded: (onboarded) => {
    lsSet(LS_KEYS.onboarded, String(onboarded))
    set({ onboarded })
    patchServer({ onboarded })
  },
  chatOpen: true,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  setChatOpen: (chatOpen) => set({ chatOpen }),
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
  instanceName: initialInstanceName,
  setInstanceName: (instanceName) => {
    lsSet(LS_KEYS.instanceName, instanceName)
    set({ instanceName })
    patchServer({ instance_name: instanceName })
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
  features: applyFeatureOrder([
    { label: 'Chat', enabled: true },
    { label: 'Tasks', enabled: true },
    { label: 'Agents', enabled: true },
    { label: 'Activity', enabled: true },
    { label: 'Projects', enabled: true },
    { label: 'Drive', enabled: true },
    { label: 'Calendar', enabled: true },
    { label: 'Gmail', enabled: true },
    { label: 'Slack', enabled: true },
    { label: 'GitHub', enabled: true },
    { label: 'Specs', enabled: true },
    { label: 'Automations', enabled: true },
    { label: 'Cost Tracking', enabled: true },
  ]),
  setFeatures: (features) => {
    lsSet(LS_KEYS.featureOrder, JSON.stringify(features.map((f) => f.label)))
    set({ features })
  },
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
  sideBySideEnabled: initialSideBySideEnabled,
  setSideBySideEnabled: (sideBySideEnabled) => {
    lsSet(LS_KEYS.sideBySideEnabled, String(sideBySideEnabled))
    set({ sideBySideEnabled })
    patchServer({ side_by_side_enabled: sideBySideEnabled })
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
  agentsLastViewed: initialAgentsLastViewed,
  setAgentsLastViewed: (agentsLastViewed) => {
    lsSet(LS_KEYS.agentsLastViewed, agentsLastViewed)
    set({ agentsLastViewed })
    patchServer({ agents_last_viewed: agentsLastViewed })
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
  // Appearance settings
  sidebarPosition: initialSidebarPosition,
  setSidebarPosition: (sidebarPosition) => {
    lsSet(LS_KEYS.sidebarPosition, sidebarPosition)
    set({ sidebarPosition })
    patchServer({ sidebar_position: sidebarPosition })
  },
  compactMode: initialCompactMode,
  setCompactMode: (compactMode) => {
    lsSet(LS_KEYS.compactMode, String(compactMode))
    set({ compactMode })
    patchServer({ compact_mode: compactMode })
  },
  fontSize: initialFontSize,
  setFontSize: (fontSize) => {
    lsSet(LS_KEYS.fontSize, fontSize)
    set({ fontSize })
    patchServer({ font_size: fontSize })
  },
  iconStyle: initialIconStyle,
  setIconStyle: (iconStyle) => {
    lsSet(LS_KEYS.iconStyle, iconStyle)
    set({ iconStyle })
    patchServer({ icon_style: iconStyle })
  },
  cardStyle: initialCardStyle,
  setCardStyle: (cardStyle) => {
    lsSet(LS_KEYS.cardStyle, cardStyle)
    set({ cardStyle })
    patchServer({ card_style: cardStyle })
  },
  dashboardLayout: initialDashboardLayout,
  setDashboardLayout: (dashboardLayout) => {
    lsSet(LS_KEYS.dashboardLayout, dashboardLayout)
    set({ dashboardLayout })
    patchServer({ dashboard_layout: dashboardLayout })
  },
  statusDotStyle: initialStatusDotStyle,
  setStatusDotStyle: (statusDotStyle) => {
    lsSet(LS_KEYS.statusDotStyle, statusDotStyle)
    set({ statusDotStyle })
    patchServer({ status_dot_style: statusDotStyle })
  },
  greetingStyle: initialGreetingStyle,
  setGreetingStyle: (greetingStyle) => {
    lsSet(LS_KEYS.greetingStyle, greetingStyle)
    set({ greetingStyle })
    patchServer({ greeting_style: greetingStyle })
  },
  showBudgetCaps: initialShowBudgetCaps,
  setShowBudgetCaps: (showBudgetCaps) => {
    lsSet(LS_KEYS.showBudgetCaps, String(showBudgetCaps))
    set({ showBudgetCaps })
    patchServer({ show_budget_caps: showBudgetCaps })
  },
  // Optimistic pending-spec queue. Session-only, never persisted.
  pendingSpecs: {},
  addPendingSpec: (spec) => {
    set((s) => ({
      pendingSpecs: { ...s.pendingSpecs, [spec.tempId]: spec },
    }))
  },
  updatePendingSpec: (tempId, patch) => {
    set((s) => {
      const existing = s.pendingSpecs[tempId]
      if (!existing) return s
      return {
        pendingSpecs: {
          ...s.pendingSpecs,
          [tempId]: { ...existing, ...patch },
        },
      }
    })
  },
  removePendingSpec: (tempId) => {
    set((s) => {
      if (!s.pendingSpecs[tempId]) return s
      const next = { ...s.pendingSpecs }
      delete next[tempId]
      return { pendingSpecs: next }
    })
  },
  enterpriseUser: null,
  instanceMode: initialInstanceMode,
  setInstanceMode: (instanceMode) => {
    if (!TEAM_MODE_VISIBLE && instanceMode === 'team') return
    lsSet(LS_KEYS.instanceMode, instanceMode)
    set({ instanceMode })
    patchServer({ instance_mode: instanceMode })
  },
  orgName: initialOrgName,
  setOrgName: (orgName) => {
    lsSet(LS_KEYS.orgName, orgName)
    set({ orgName })
  },
  teamAccentColor: initialTeamAccentColor,
  setTeamAccentColor: (teamAccentColor) => {
    lsSet(LS_KEYS.teamAccentColor, teamAccentColor)
    set({ teamAccentColor })
  },
  displayOsName: () => {
    const s = get()
    if (s.instanceMode === 'team' && s.orgName) {
      return s.orgName + ' OS'
    }
    return s.osName
  },
  hydrateFromServer: async () => {
    let server: Record<string, unknown> = {}
    try {
      server = await api.get<Record<string, unknown>>('/settings')
    } catch (e) {
      console.error('settings hydration failed:', e)
      set({ hydrated: true })
      return
    }

    // Fresh-start sweep: when the server reports onboarded=false, any
    // localStorage flag carrying the ``myos-ephemeral-`` prefix is
    // treated as throwaway state from a prior session and removed.
    // Layout (and therefore ReleaseNotesWatcher) only mounts once the
    // user passes the wizard, so clearing inside the watcher misses
    // the window. Clearing here, in the hydration pass that runs
    // BEFORE the wizard ever mounts, guarantees a clean slate: the
    // release-notes modal can re-celebrate the same spec, the
    // side-by-side toggle returns to the Solo default, the All-pill
    // pulse dedup is cleared, and any future ephemeral flag added
    // with this prefix auto-clears on reset without needing another
    // edit here.
    if (server.onboarded === false) {
      try {
        for (const key of Object.keys(window.localStorage)) {
          if (key.startsWith('myos-ephemeral-')) {
            window.localStorage.removeItem(key)
          }
        }
      } catch {
        // ignore storage errors, the in-memory state will still be
        // reset below via the normal hydration updates
      }
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

    // instance_name. The user-picked name for this specific install.
    // Defaults to the product name ("myOS") so every surface reads
    // correctly for a user who has not renamed their copy.
    if (hasValue(server.instance_name)) {
      const v = String(server.instance_name)
      updates.instanceName = v
      lsSet(LS_KEYS.instanceName, v)
    } else if (state.instanceName && state.instanceName !== 'myOS') {
      backfill.instance_name = state.instanceName
    }

    // instance_mode
    if (hasValue(server.instance_mode)) {
      let v = String(server.instance_mode) as InstanceMode
      // When team mode is hidden, ignore a stale 'team' value from the
      // server. The user may have set it in a previous session before
      // the flag was turned off, but surfacing team UI would be wrong.
      if (!TEAM_MODE_VISIBLE && v === 'team') v = 'personal'
      updates.instanceMode = v
      lsSet(LS_KEYS.instanceMode, v)
    } else if (state.instanceMode !== 'personal') {
      backfill.instance_mode = state.instanceMode
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

    // agents_last_viewed
    if (hasValue(server.agents_last_viewed)) {
      const v = String(server.agents_last_viewed)
      updates.agentsLastViewed = v
      lsSet(LS_KEYS.agentsLastViewed, v)
    } else if (state.agentsLastViewed) {
      backfill.agents_last_viewed = state.agentsLastViewed
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

    // Appearance settings hydration
    const appearanceFields: Array<{
      serverKey: string
      storeKey: keyof AppState
      lsKey: string
      defaultVal: string | boolean
    }> = [
      { serverKey: 'sidebar_position', storeKey: 'sidebarPosition', lsKey: LS_KEYS.sidebarPosition, defaultVal: 'left' },
      { serverKey: 'compact_mode', storeKey: 'compactMode', lsKey: LS_KEYS.compactMode, defaultVal: false },
      { serverKey: 'font_size', storeKey: 'fontSize', lsKey: LS_KEYS.fontSize, defaultVal: 'medium' },
      { serverKey: 'icon_style', storeKey: 'iconStyle', lsKey: LS_KEYS.iconStyle, defaultVal: 'filled' },
      { serverKey: 'card_style', storeKey: 'cardStyle', lsKey: LS_KEYS.cardStyle, defaultVal: 'glass' },
      { serverKey: 'dashboard_layout', storeKey: 'dashboardLayout', lsKey: LS_KEYS.dashboardLayout, defaultVal: 'full' },
      { serverKey: 'status_dot_style', storeKey: 'statusDotStyle', lsKey: LS_KEYS.statusDotStyle, defaultVal: 'dots' },
      { serverKey: 'greeting_style', storeKey: 'greetingStyle', lsKey: LS_KEYS.greetingStyle, defaultVal: 'time' },
    ]
    for (const field of appearanceFields) {
      if (hasValue(server[field.serverKey])) {
        const v = typeof field.defaultVal === 'boolean'
          ? Boolean(server[field.serverKey])
          : String(server[field.serverKey])
        ;(updates as Record<string, unknown>)[field.storeKey] = v
        lsSet(field.lsKey, String(v))
      } else {
        const current = state[field.storeKey]
        if (current !== field.defaultVal) {
          backfill[field.serverKey] = current
        }
      }
    }

    // features (sidebar toggles). The server stores a flat object like
    // {Chat: true, Tasks: true, Automations: false}. Merge with the
    // client-side defaults so new features appear and removed features
    // disappear, but the enabled/disabled state from the server wins.
    if (hasValue(server.features) && typeof server.features === 'object') {
      const serverFeatures = server.features as Record<string, boolean>
      const merged = state.features.map((f) => {
        const serverVal = serverFeatures[f.label]
        return serverVal !== undefined ? { ...f, enabled: serverVal } : f
      })
      updates.features = applyFeatureOrder(merged)
    } else {
      // Backfill: send client defaults to server so they persist.
      const featuresObj: Record<string, boolean> = {}
      state.features.forEach((f) => { featuresObj[f.label] = f.enabled })
      backfill.features = featuresObj
    }

    if (Object.keys(updates).length > 0) {
      set(updates)
    }
    if (Object.keys(backfill).length > 0) {
      patchServer(backfill)
    }
    set({ hydrated: true })

    // Fetch enterprise user identity and org info
    try {
      const me = await api.get<{ authenticated: boolean; enterprise: boolean; email?: string; role?: string }>('/enterprise/me')
      if (me.authenticated && me.email) {
        set({ enterpriseUser: { authenticated: true, email: me.email, role: me.role || 'member' } })
      } else {
        set({ enterpriseUser: null })
      }
      // If enterprise is active, fetch org name and auto-detect team mode
      if (me.enterprise) {
        try {
          const ent = await api.get<{ org?: { name?: string } }>('/enterprise')
          if (ent.org?.name) {
            lsSet(LS_KEYS.orgName, ent.org.name)
            set({ orgName: ent.org.name })
          }
        } catch { /* org fetch is non-blocking */ }
        // Auto-migrate: if enterprise is active but instance mode was never set to team.
        // Only do this when team mode is visible — if the flag is off, enterprise
        // detection must not silently switch the user into team mode.
        const current = get()
        if (TEAM_MODE_VISIBLE && current.instanceMode === 'personal') {
          lsSet(LS_KEYS.instanceMode, 'team')
          set({ instanceMode: 'team' })
          patchServer({ instance_mode: 'team' })
        }
      }
    } catch (e) {
      console.error('enterprise user fetch failed:', e)
      set({ hydrated: true })
      set({ enterpriseUser: null })
    }
  },
}))
