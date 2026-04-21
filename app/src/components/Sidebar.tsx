import { useState, useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import Icon from './Icon'
import WhatsNew from './WhatsNew'
import { useAppStore } from '../stores/app'
import AdminSection from './AdminSection'
import { api } from '../lib/api'
import { onAgentsChange, onTasksChange, onSpecsChange, isDismissed } from '../lib/sidebarBus'
import { isUserSpawnedAgent } from '../lib/agentUtils'

// ------------- types -------------

interface NavItem {
  to: string
  icon: string
  label: string
  featureLabel: string | null
  badge?: boolean
  gmailBadge?: boolean
  tasksBadge?: boolean
  specsBadge?: boolean
}

interface NavGroup {
  id: string
  label: string
  icon: string
  items: NavItem[]
}

// ------------- constants -------------

// localStorage key for per-group collapsed state.
// Value is a JSON object: { [groupId]: boolean }
const COLLAPSED_KEY = 'sidebar-group-collapsed'

const TOP_LEVEL_ROUTES = new Set(['/', '/tasks', '/agents', '/activity'])

const NAV_GROUPS: NavGroup[] = [
  {
    id: 'files',
    label: 'Files & Docs',
    icon: 'folder_open',
    items: [
      { to: '/files', icon: 'folder', label: 'Files', featureLabel: 'Projects' },
      { to: '/drive', icon: 'cloud', label: 'Drive', featureLabel: 'Drive' },
      { to: '/specs', icon: 'description', label: 'Specs', featureLabel: 'Specs', specsBadge: true },
    ],
  },
  {
    id: 'comms',
    label: 'Comms',
    icon: 'forum',
    items: [
      { to: '/gmail', icon: 'mail', label: 'Gmail', featureLabel: 'Gmail', gmailBadge: true },
      { to: '/calendar', icon: 'calendar_month', label: 'Calendar', featureLabel: 'Calendar' },
      { to: '/imessage', icon: 'chat_bubble', label: 'iMessage', featureLabel: 'iMessage' },
      { to: '/slack', icon: 'chat', label: 'Slack', featureLabel: 'Slack' },
      { to: '/github', icon: 'code', label: 'GitHub', featureLabel: 'GitHub' },
    ],
  },
]

// All items for route-based lookups (preserves featureLabel for feature filtering)
const ALL_NAV_ITEMS: NavItem[] = [
  { to: '/', icon: 'home', label: 'Home', featureLabel: null },
  { to: '/tasks', icon: 'checklist', label: 'Tasks', featureLabel: 'Tasks', tasksBadge: true },
  { to: '/agents', icon: 'smart_toy', label: 'Agents', badge: true, featureLabel: 'Agents' },
  { to: '/activity', icon: 'history', label: 'Activity', featureLabel: 'Activity' },
  ...NAV_GROUPS.flatMap((g) => g.items),
]

// Usage sits at the bottom next to Settings, not inside a group
const USAGE_NAV_ITEM: NavItem = { to: '/costs', icon: 'payments', label: 'Usage', featureLabel: 'Cost Tracking' }

// ------------- helpers -------------

function loadCollapsedState(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '{}')
  } catch {
    return {}
  }
}

function saveCollapsedState(state: Record<string, boolean>) {
  localStorage.setItem(COLLAPSED_KEY, JSON.stringify(state))
}

/** Returns the group id that contains the given pathname, or null. */
function groupForPath(pathname: string): string | null {
  for (const group of NAV_GROUPS) {
    if (group.items.some((item) => item.to === pathname)) return group.id
  }
  return null
}

// ------------- SortableNavItem (unchanged logic) -------------

function SortableNavItem({ item, linkClass, activeAgents, gmailUnread, openTasksCount, unfinishedSpecs, onNavigate, iconFilled }: {
  item: NavItem
  linkClass: (isActive: boolean) => string
  activeAgents: number
  gmailUnread: number
  openTasksCount: number
  unfinishedSpecs: number
  onNavigate?: () => void
  iconFilled: 'filled' | 'outlined'
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.to })
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  }
  return (
    <div ref={setNodeRef} style={style} className="flex items-center">
      <button
        type="button"
        {...attributes}
        {...listeners}
        className="text-slate-700 hover:text-slate-500 cursor-grab active:cursor-grabbing touch-none px-0.5 shrink-0"
        tabIndex={-1}
      >
        <Icon name="drag_indicator" className="text-xs" />
      </button>
      <NavLink
        to={item.to}
        end={item.to === '/'}
        onClick={onNavigate}
        className={({ isActive }) => linkClass(isActive)}
      >
        {({ isActive }) => (
          <>
            <Icon name={item.icon} filled={iconFilled === 'filled' ? true : isActive} className="text-xl" />
            <span className="text-sm font-medium">{item.label}</span>
            {item.badge && activeAgents > 0 && (
              <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                {activeAgents}
              </span>
            )}
            {item.gmailBadge && gmailUnread > 0 && (
              <span className="ml-auto bg-red-500/20 text-red-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                {gmailUnread}
              </span>
            )}
            {item.tasksBadge && openTasksCount > 0 && (
              <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                {openTasksCount}
              </span>
            )}
            {item.specsBadge && unfinishedSpecs > 0 && (
              <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                {unfinishedSpecs}
              </span>
            )}
          </>
        )}
      </NavLink>
    </div>
  )
}

// ------------- SidebarGroup -------------

interface SidebarGroupProps {
  group: NavGroup
  visibleItems: NavItem[]
  collapsed: boolean
  onToggle: () => void
  linkClass: (isActive: boolean) => string
  activeAgents: number
  gmailUnread: number
  openTasksCount: number
  unfinishedSpecs: number
  onNavigate?: () => void
  iconFilled: 'filled' | 'outlined'
  features: { label: string; enabled: boolean }[]
  setFeatures: (f: { label: string; enabled: boolean }[]) => void
}

function SidebarGroup({
  group,
  visibleItems,
  collapsed,
  onToggle,
  linkClass,
  activeAgents,
  gmailUnread,
  openTasksCount,
  unfinishedSpecs,
  onNavigate,
  iconFilled,
  features,
  setFeatures,
}: SidebarGroupProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  // Rolled-up badge: sum of badges from sub-items when collapsed
  const rolledBadge = collapsed ? (() => {
    let total = 0
    for (const item of visibleItems) {
      if (item.gmailBadge && gmailUnread > 0) total += gmailUnread
    }
    return total
  })() : 0

  const rolledAgents = collapsed && group.items.some((i) => i.badge) && activeAgents > 0 ? activeAgents : 0

  const rolledSpecs = collapsed && group.items.some((i) => i.specsBadge) && unfinishedSpecs > 0 ? unfinishedSpecs : 0

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = visibleItems.findIndex((item) => item.to === active.id)
    const newIndex = visibleItems.findIndex((item) => item.to === over.id)
    if (oldIndex === -1 || newIndex === -1) return
    const reordered = arrayMove(visibleItems, oldIndex, newIndex)
    // Rebuild features array preserving items outside this group
    const reorderedLabels = reordered.map((i) => i.featureLabel).filter(Boolean) as string[]
    const seen = new Set(reorderedLabels)
    const newFeatures: { label: string; enabled: boolean }[] = [
      ...reorderedLabels.map((l) => features.find((f) => f.label === l)!).filter(Boolean),
      ...features.filter((f) => !seen.has(f.label)),
    ]
    setFeatures(newFeatures)
  }

  return (
    <div data-testid={`group-${group.id}`}>
      {/* Group header */}
      <button
        type="button"
        data-testid={`group-header-${group.id}`}
        onClick={onToggle}
        className="flex items-center gap-3 w-full px-4 py-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800/30 transition-colors duration-150 cursor-pointer select-none"
      >
        <Icon name={group.icon} className="text-base" />
        <span className="text-xs font-semibold uppercase tracking-wider flex-1 text-left">{group.label}</span>
        {(rolledBadge > 0) && (
          <span className="bg-red-500/20 text-red-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
            {rolledBadge}
          </span>
        )}
        {(rolledAgents > 0) && (
          <span className="flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            {rolledAgents}
          </span>
        )}
        {(rolledSpecs > 0) && (
          <span className="flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            {rolledSpecs}
          </span>
        )}
        <Icon
          name="chevron_right"
          className={`text-base transition-transform duration-200 ${collapsed ? '' : 'rotate-90'}`}
        />
      </button>

      {/* Group items */}
      {!collapsed && (
        <div data-testid={`group-items-${group.id}`} className="ml-2">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={visibleItems.map((item) => item.to)}
              strategy={verticalListSortingStrategy}
            >
              {visibleItems.map((item) => (
                <SortableNavItem
                  key={item.to}
                  item={item}
                  linkClass={linkClass}
                  activeAgents={activeAgents}
                  gmailUnread={gmailUnread}
                  openTasksCount={openTasksCount}
                  unfinishedSpecs={unfinishedSpecs}
                  onNavigate={onNavigate}
                  iconFilled={iconFilled}
                />
              ))}
            </SortableContext>
          </DndContext>
        </div>
      )}
    </div>
  )
}

// ------------- Sidebar -------------

export function Sidebar() {
  const displayOsName = useAppStore((s) => s.displayOsName())
  const instanceMode = useAppStore((s) => s.instanceMode)
  const features = useAppStore((s) => s.features)
  const setFeatures = useAppStore((s) => s.setFeatures)
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)
  const sidebarPosition = useAppStore((s) => s.sidebarPosition)
  const iconStyle = useAppStore((s) => s.iconStyle)
  const statusDotStyle = useAppStore((s) => s.statusDotStyle)
  const darkMode = useAppStore((s) => s.darkMode)
  const toggleDarkMode = useAppStore((s) => s.toggleDarkMode)
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const [activeAgents, setActiveAgents] = useState(0)
  const [openTasksCount, setOpenTasksCount] = useState(0)
  const [unfinishedSpecs, setUnfinishedSpecs] = useState(0)
  const [gmailUnread, setGmailUnread] = useState(0)
  const [version, setVersion] = useState('')
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [ostkUp, setOstkUp] = useState<boolean | null>(null)
  const [ostkKernel, setOstkKernel] = useState('')
  const [sessionCount, setSessionCount] = useState(0)
  const [llmOk, setLlmOk] = useState<boolean | null>(null)

  // Pull the configured chat model from the store so the dot can label
  // itself with the active provider ("Claude", "Gemini", etc.). The
  // store keeps this as a bare key like "claude" or "gemini".
  const defaultChatModel = useAppStore((s) => s.defaultChatModel)
  const llmProviderLabel = (() => {
    const m = (defaultChatModel || '').toLowerCase()
    if (m.includes('claude')) return 'Claude'
    if (m.includes('gemini')) return 'Gemini'
    return 'LLM'
  })()

  // Collapsed state per group. Initialized from localStorage; active group
  // is forced open if it would otherwise be collapsed.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    const saved = loadCollapsedState()
    const activeGroup = groupForPath(location.pathname)
    if (activeGroup && saved[activeGroup]) {
      // Ensure active group starts expanded
      return { ...saved, [activeGroup]: false }
    }
    return saved
  })

  // When the route changes, auto-expand the group that owns the new route
  useEffect(() => {
    const activeGroup = groupForPath(location.pathname)
    if (activeGroup) {
      setCollapsed((prev) => {
        if (prev[activeGroup] === false) return prev
        const next = { ...prev, [activeGroup]: false }
        saveCollapsedState(next)
        return next
      })
    }
  }, [location.pathname])

  function toggleGroup(groupId: string) {
    setCollapsed((prev) => {
      const next = { ...prev, [groupId]: !prev[groupId] }
      saveCollapsedState(next)
      return next
    })
  }

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        // Compact server-side summary endpoint: already filters to
        // status=running, source=claude-code, limit=20. Avoids pulling
        // the full ~337KB /agents payload just to compute a count, and
        // eliminates the 0-vs-1 render race between sidebar and the
        // Active Sessions page by making both read from the same
        // server-filtered set. Keeps isUserSpawnedAgent + isDismissed
        // client-side because the server does not know about
        // locally-dismissed agents and the server filter does not
        // re-check isMainSession.
        interface SummaryAgent { name: string; status: string; source?: string; model?: string; description?: string; spawned_at?: string; last_heartbeat_at?: string }
        const res = await api.get<{ agents: SummaryAgent[] }>(
          '/agents?summary=1&status=running&source=claude-code&limit=20'
        )
        const userSpawnedRunning = (res.agents || []).filter(
          (a) => isUserSpawnedAgent(a) && !isDismissed(a.name)
        )
        setActiveAgents(userSpawnedRunning.length)
      } catch {
        // ignore
      }
    }
    fetchAgents()
    // Poll every 2 seconds as a safety net so background changes (another
    // tab, a subagent completing on its own) are still reflected within
    // two seconds even when nothing in this tab triggered a bump.
    const interval = setInterval(fetchAgents, 2000)
    // Any write to /agents/* from this tab refetches immediately so the
    // badge updates within milliseconds of the user's action.
    const unsubscribe = onAgentsChange(() => { fetchAgents() })
    return () => {
      clearInterval(interval)
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    const fetchTaskCounts = async () => {
      try {
        const res = await api.get<{ open: number }>('/tasks/counts')
        setOpenTasksCount(res.open ?? 0)
      } catch {
        // ignore
      }
    }
    fetchTaskCounts()
    // Poll every 2 seconds as a safety net. Any write to /tasks/* in this
    // tab also triggers an immediate refetch via the sidebar bus.
    const interval = setInterval(fetchTaskCounts, 2000)
    const unsubscribe = onTasksChange(() => { fetchTaskCounts() })
    return () => {
      clearInterval(interval)
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    const fetchSpecCounts = async () => {
      try {
        // Backend-filtered count of specs that have not reached the
        // terminal "complete" state (covers draft + ready + in-progress).
        // Same shape pattern as /tasks/counts so the badge stays in sync
        // with the Specs page default view without the frontend having
        // to know the spec lifecycle vocabulary.
        const res = await api.get<{ unfinished: number }>('/specs/counts')
        setUnfinishedSpecs(res.unfinished ?? 0)
      } catch {
        // ignore
      }
    }
    fetchSpecCounts()
    // Poll every 2 seconds to match the Agents and Tasks badges so all
    // three refresh on the same cadence. Any write to /specs/* in this
    // tab also bumps the bus for immediate refresh.
    const interval = setInterval(fetchSpecCounts, 2000)
    const unsubscribe = onSpecsChange(() => { fetchSpecCounts() })
    return () => {
      clearInterval(interval)
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    const fetchGmail = async () => {
      try {
        const res = await api.get<{ authenticated: boolean; unread_count: number }>('/gmail/auth/status')
        if (res.authenticated) {
          setGmailUnread(res.unread_count ?? 0)
        }
      } catch {
        // ignore
      }
    }
    fetchGmail()
    const interval = setInterval(fetchGmail, 60000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    const fetchSessions = async () => {
      try {
        const res = await api.get<{ active_count: number }>('/sessions/active')
        setSessionCount(res.active_count ?? 0)
      } catch {
        // ignore
      }
    }
    fetchSessions()
    const interval = setInterval(fetchSessions, 10000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    api.get<{ myos: { current: string } }>('/upgrade/status')
      .then((res) => setVersion(res.myos?.current ?? ''))
      .catch(() => {})
  }, [])

  // Fetch the most recent LLM probe result once on mount. The backend
  // already persists the last run in settings, so we reuse that cached
  // value and piggyback on the backend/ostk health cycle instead of
  // introducing a new polling loop. The user can rerun the probe from
  // Settings if they need a fresh check.
  useEffect(() => {
    let cancelled = false
    api
      .get<{ last_probe_result: { status?: string } | null }>('/settings/probe')
      .then((res) => {
        if (cancelled) return
        const status = res.last_probe_result?.status
        if (!status) {
          setLlmOk(null)
          return
        }
        // Accept "ok" (probe_runner shape) or "pass" (probes shape)
        setLlmOk(status === 'ok' || status === 'pass')
      })
      .catch(() => {
        if (!cancelled) setLlmOk(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    let consecutiveFailures = 0
    const SUCCESS_INTERVAL = 15_000
    const FAILURE_INTERVAL = 2_000
    const FAILURE_THRESHOLD = 2

    const scheduleNext = (delayMs: number) => {
      if (cancelled) return
      timer = setTimeout(() => {
        void checkHealth()
      }, delayMs)
    }

    const checkHealth = async () => {
      try {
        const res = await api.get<{ kernel: string }>('/status/clock')
        if (cancelled) return
        consecutiveFailures = 0
        setBackendUp(true)
        const k = res.kernel || ''
        setOstkUp(k !== 'unknown' && k !== '')
        setOstkKernel(k)
        scheduleNext(SUCCESS_INTERVAL)
      } catch {
        if (cancelled) return
        consecutiveFailures += 1
        if (consecutiveFailures >= FAILURE_THRESHOLD) {
          setBackendUp(false)
          setOstkUp(false)
          setOstkKernel('')
        }
        scheduleNext(FAILURE_INTERVAL)
      }
    }
    void checkHealth()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  // Build feature-filtered view of all nav items
  const featureOrder = new Map(features.map((f, i) => [f.label, i]))

  function isEnabled(item: NavItem): boolean {
    if (!item.featureLabel) return true
    const feature = features.find((f) => f.label === item.featureLabel)
    return feature ? feature.enabled : true
  }

  function sortedItems(items: NavItem[]): NavItem[] {
    return items
      .filter(isEnabled)
      .sort((a, b) => {
        if (!a.featureLabel) return -1
        if (!b.featureLabel) return 1
        return (featureOrder.get(a.featureLabel) ?? 999) - (featureOrder.get(b.featureLabel) ?? 999)
      })
  }

  // Top-level items (Home + Tasks + Agents + Activity) that are not in any group
  const topLevelItems = ALL_NAV_ITEMS.filter((i) => TOP_LEVEL_ROUTES.has(i.to) && isEnabled(i)).sort((a, b) => {
    const order = ['/', '/tasks', '/agents', '/activity']
    return order.indexOf(a.to) - order.indexOf(b.to)
  })

  const usageEnabled = isEnabled(USAGE_NAV_ITEM)

  const linkClass = (isActive: boolean) =>
    `group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer active:scale-[0.98] ${
      isActive
        ? 'accent-highlight border-r-2 accent-border'
        : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50'
    }`

  return (
    <>
      {/* Mobile hamburger button */}
      <button
        onClick={() => setMobileOpen(true)}
        className={`lg:hidden fixed top-4 z-50 p-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-300 hover:text-white ${sidebarPosition === 'right' ? 'right-4' : 'left-4'}`}
        aria-label="Open menu"
      >
        <Icon name="menu" className="text-xl" />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setMobileOpen(false)}
        />
      )}

    <aside data-tour="sidebar" className={`h-screen w-56 fixed top-0 ${sidebarPosition === 'right' ? 'right-0 border-l' : 'left-0 border-r'} border-slate-800 bg-slate-950 shadow-2xl flex flex-col py-6 z-50 transition-transform duration-200 ${mobileOpen ? 'translate-x-0' : sidebarPosition === 'right' ? 'translate-x-full' : '-translate-x-full'} lg:translate-x-0`}>
      <div className="px-5 mb-8">
        <span className={`text-xl font-black tracking-tight ${instanceMode === 'team' ? 'team-text' : 'accent-text'}`}>{displayOsName}</span>
        {instanceMode === 'team' && (
          <span className="ml-2 text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400 align-middle">TEAM</span>
        )}
        {version && (
          <span className="block text-[10px] text-slate-500 font-mono mt-0.5">{version}</span>
        )}
      </div>

      <nav className="flex flex-col gap-0.5 px-3 flex-1 overflow-y-auto">
        {/* Top-level items: Home, Tasks, Agents, Activity */}
        {topLevelItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => linkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name={item.icon} filled={iconStyle === 'filled' ? true : isActive} className="text-xl" />
                <span className="text-sm font-medium">{item.label}</span>
                {item.badge && activeAgents > 0 && (
                  <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                    {activeAgents}
                  </span>
                )}
                {item.tasksBadge && openTasksCount > 0 && (
                  <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                    {openTasksCount}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}

        {/* Divider */}
        <div className="my-1.5 border-t border-slate-800/60" />

        {/* Collapsible groups */}
        {NAV_GROUPS.map((group) => {
          const visibleItems = sortedItems(group.items)
          if (visibleItems.length === 0) return null
          return (
            <SidebarGroup
              key={group.id}
              group={group}
              visibleItems={visibleItems}
              collapsed={!!collapsed[group.id]}
              onToggle={() => toggleGroup(group.id)}
              linkClass={linkClass}
              activeAgents={activeAgents}
              gmailUnread={gmailUnread}
              openTasksCount={openTasksCount}
              unfinishedSpecs={unfinishedSpecs}
              onNavigate={() => setMobileOpen(false)}
              iconFilled={iconStyle}
              features={features}
              setFeatures={setFeatures}
            />
          )
        })}
      </nav>

      <div className="px-3 flex flex-col gap-1">
        <WhatsNew />
        <button
          data-testid="tour-button"
          onClick={() => { useAppStore.getState().setShowTour(true); setMobileOpen(false); }}
          className="group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer text-slate-400 hover:text-slate-100 hover:bg-slate-800/50"
        >
          <Icon name="explore" filled={iconStyle === 'filled'} className="text-xl" />
          <span className="text-sm font-medium">Tour</span>
        </button>
        {instanceMode === 'team' && enterpriseUser?.role === 'admin' && (
          <AdminSection
            linkClass={linkClass}
            iconStyle={iconStyle}
            onNavigate={() => setMobileOpen(false)}
          />
        )}
        {instanceMode === 'team' && enterpriseUser && enterpriseUser.role !== 'admin' && (
          <NavLink
            to="/admin"
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => linkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name="groups" filled={iconStyle === 'filled' ? true : isActive} className="text-xl" />
                <span className="text-sm font-medium">Team</span>
              </>
            )}
          </NavLink>
        )}
        {usageEnabled && (
          <NavLink
            data-testid="usage-nav-link"
            to={USAGE_NAV_ITEM.to}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => linkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name={USAGE_NAV_ITEM.icon} filled={iconStyle === 'filled' ? true : isActive} className="text-xl" />
                <span className="text-sm font-medium">{USAGE_NAV_ITEM.label}</span>
              </>
            )}
          </NavLink>
        )}
        <NavLink
          to="/settings"
          onClick={() => setMobileOpen(false)}
          className={({ isActive }) => linkClass(isActive)}
        >
          {({ isActive }) => (
            <>
              <Icon name="settings" filled={iconStyle === 'filled' ? true : isActive} className="text-xl" />
              <span className="text-sm font-medium">Settings</span>
            </>
          )}
        </NavLink>
      </div>

      {/* Theme toggle */}
      <div className="px-4 pt-3 pb-2 mt-3 border-t border-slate-800" data-testid="theme-toggle-container">
        <button
          data-testid="theme-toggle"
          aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
          onClick={toggleDarkMode}
          className="relative flex items-center w-full h-10 rounded-full bg-slate-800 hover:bg-slate-700 transition-colors duration-200 cursor-pointer border border-slate-700 p-1"
        >
          {/* Sun icon (light) */}
          <span className={`w-1/2 flex items-center justify-center z-10 transition-colors duration-200 ${!darkMode ? 'text-amber-400' : 'text-slate-500 hover:text-slate-400'}`}>
            <Icon name="light_mode" size={20} />
          </span>
          {/* Moon icon (dark) */}
          <span className={`w-1/2 flex items-center justify-center z-10 transition-colors duration-200 ${darkMode ? 'text-blue-400' : 'text-slate-500 hover:text-slate-400'}`}>
            <Icon name="dark_mode" size={20} />
          </span>
          {/* Sliding indicator */}
          <span
            data-testid="theme-toggle-indicator"
            className={`absolute top-1 bottom-1 left-1 w-[calc(50%-4px)] rounded-full bg-slate-600 transition-transform duration-200 ${darkMode ? 'translate-x-[100%]' : 'translate-x-0'}`}
          />
        </button>
      </div>

      {/* System status indicators */}
      <div className="px-5 pt-3 pb-2 border-t border-slate-800/50 flex flex-col gap-1.5">
        {statusDotStyle === 'badges' ? (
          <>
            <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${backendUp === null ? 'bg-slate-700 text-slate-400' : backendUp ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              Backend {backendUp === null ? '' : backendUp ? 'up' : 'down'}
            </span>
            <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${ostkUp === null ? 'bg-slate-700 text-slate-400' : ostkUp ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              System{ostkKernel ? ` ${ostkKernel}` : ''} {ostkUp === null ? '' : ostkUp ? 'running' : 'offline'}
            </span>
            <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${llmOk === null ? 'bg-slate-700 text-slate-400' : llmOk ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              {llmProviderLabel} {llmOk === null ? '' : llmOk ? 'ready' : 'not responding'}
            </span>
            <NavLink
              to="/activity"
              className="hover:opacity-80 transition-opacity"
              onClick={() => setMobileOpen(false)}
            >
              <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${sessionCount > 0 ? 'bg-green-500/20 text-green-400' : 'bg-slate-700 text-slate-400'}`}>
                {sessionCount === 0 ? 'No sessions' : sessionCount === 1 ? '1 session' : `${sessionCount} sessions`}
              </span>
            </NavLink>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${backendUp === null ? 'bg-slate-600' : backendUp ? 'bg-green-400' : 'bg-red-400'}`} />
              <span className="text-[10px] text-slate-500">Backend</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${ostkUp === null ? 'bg-slate-600' : ostkUp ? 'bg-green-400' : 'bg-red-400'}`} />
              <span className="text-[10px] text-slate-500">System{ostkKernel ? ` ${ostkKernel}` : ''}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${llmOk === null ? 'bg-slate-600' : llmOk ? 'bg-green-400' : 'bg-red-400'}`} />
              <span className="text-[10px] text-slate-500">{llmProviderLabel}</span>
            </div>
            <NavLink
              to="/activity"
              className="flex items-center gap-2 hover:opacity-80 transition-opacity"
              onClick={() => setMobileOpen(false)}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${sessionCount > 1 ? 'bg-green-400 animate-pulse' : sessionCount === 1 ? 'bg-green-400' : 'bg-slate-600'}`} />
              <span className="text-[10px] text-slate-500">
                {sessionCount === 0 ? 'No sessions' : sessionCount === 1 ? '1 session' : `${sessionCount} sessions`}
              </span>
            </NavLink>
          </>
        )}
      </div>
    </aside>
    </>
  )
}
