import { useState, useEffect, useCallback } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
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
import ConfirmModal from './ConfirmModal'
import { useAppStore, TEAM_MODE_VISIBLE } from '../stores/app'
import AdminSection from './AdminSection'
import { api } from '../lib/api'
import { onTasksChange, onSpecsChange } from '../lib/sidebarBus'
import { useRunningAgentsStore } from '../stores/runningAgents'

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
  backlogBadge?: boolean

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

const TOP_LEVEL_ROUTES = new Set(['/', '/backlog', '/agents', '/tasks', '/specs', '/break'])

const NAV_GROUPS: NavGroup[] = [
  {
    id: 'integrations',
    label: 'Integrations',
    icon: 'hub',
    items: [
      { to: '/gems', icon: 'auto_awesome', label: 'Gems', featureLabel: 'Gems' },
      { to: '/files', icon: 'folder', label: 'Docs', featureLabel: 'Projects' },
      { to: '/ostk', icon: 'terminal', label: 'ostk', featureLabel: 'ostk' },
      { to: '/drive', icon: 'cloud', label: 'Drive', featureLabel: 'Drive' },
      { to: '/gmail', icon: 'mail', label: 'Gmail', featureLabel: 'Gmail', gmailBadge: true },
      { to: '/calendar', icon: 'calendar_month', label: 'Calendar', featureLabel: 'Calendar' },
      { to: '/imessage', icon: 'sms', label: 'Messages', featureLabel: 'iMessage' },
      { to: '/slack', icon: 'chat', label: 'Slack', featureLabel: 'Slack' },
      { to: '/github', icon: 'code', label: 'GitHub', featureLabel: 'GitHub' },
      { to: '/jira', icon: 'bug_report', label: 'Jira', featureLabel: 'Jira' },
      { to: '/confluence', icon: 'menu_book', label: 'Confluence', featureLabel: 'Confluence' },
    ],
  },
]

// All items for route-based lookups (preserves featureLabel for feature filtering)
const ALL_NAV_ITEMS: NavItem[] = [
  { to: '/', icon: 'home', label: 'Home', featureLabel: null },
  { to: '/tasks', icon: 'checklist', label: 'Needles', featureLabel: 'Needles', tasksBadge: true },
  { to: '/specs', icon: 'article', label: 'Specs', featureLabel: 'Specs', specsBadge: true },
  { to: '/agents', icon: 'smart_toy', label: 'Agents', badge: true, featureLabel: 'Agents' },
  { to: '/backlog', icon: 'inventory_2', label: 'Kanban view', featureLabel: 'Backlog' },
  { to: '/break', icon: 'sports_esports', label: 'Break Room', featureLabel: 'Break Room' },
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
            {item.backlogBadge && (openTasksCount + unfinishedSpecs) > 0 && (
              <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                {openTasksCount + unfinishedSpecs}
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
        aria-expanded={!collapsed}
        aria-controls={`group-items-${group.id}`}
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
        <div id={`group-items-${group.id}`} data-testid={`group-items-${group.id}`} className="ml-2">
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
  const powerUserMode = useAppStore((s) => s.powerUserMode)
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)
  const sidebarPosition = useAppStore((s) => s.sidebarPosition)
  const iconStyle = useAppStore((s) => s.iconStyle)
  const statusDotStyle = useAppStore((s) => s.statusDotStyle)
  const sidebarWidth = useAppStore((s) => s.sidebarWidth)
  const setSidebarWidth = useAppStore((s) => s.setSidebarWidth)
  const isSidebarResizing = useAppStore((s) => s.isSidebarResizing)
  const setIsSidebarResizing = useAppStore((s) => s.setIsSidebarResizing)
  const darkMode = useAppStore((s) => s.darkMode)
  const toggleDarkMode = useAppStore((s) => s.toggleDarkMode)
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const handleSidebarMouseDown = useCallback(() => {
    setIsSidebarResizing(true)
  }, [setIsSidebarResizing])

  const activeAgents = useRunningAgentsStore((s) => s.count)
  const [openTasksCount, setOpenTasksCount] = useState(0)
  const [unfinishedSpecs, setUnfinishedSpecs] = useState(0)
  const [gmailUnread, setGmailUnread] = useState(0)
  const [version, setVersion] = useState('')
  const [healthState, setHealthState] = useState<'healthy' | 'checking' | 'down' | 'starting' | null>(null)
  const [lastHealthyAt, setLastHealthyAt] = useState<number | null>(null)
  const [ostkKernel, setOstkKernel] = useState('')
  const [, setSessionCount] = useState(0)

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
    if (!isSidebarResizing) return

    let pendingWidth: number | null = null
    let rafId: number | null = null

    const flush = () => {
      rafId = null
      if (pendingWidth !== null) {
        setSidebarWidth(pendingWidth)
        pendingWidth = null
      }
    }

    const handleMouseMove = (e: MouseEvent) => {
      pendingWidth = sidebarPosition === 'right'
        ? window.innerWidth - e.clientX
        : e.clientX
      if (rafId === null) {
        rafId = typeof requestAnimationFrame !== 'undefined'
          ? requestAnimationFrame(flush)
          : (setTimeout(flush, 16) as unknown as number)
      }
    }

    const handleMouseUp = () => {
      if (pendingWidth !== null) {
        setSidebarWidth(pendingWidth)
        pendingWidth = null
      }
      setIsSidebarResizing(false)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      if (rafId !== null) {
        if (typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(rafId)
        } else {
          clearTimeout(rafId)
        }
      }
    }
  }, [isSidebarResizing, setSidebarWidth, setIsSidebarResizing, sidebarPosition])


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



  // Optimistic offset applied while the Specs page has a delete in its
  // 5 s undo window. The Specs page dispatches ``myos:pending-spec-delta``
  // with delta=-1 when the user clicks delete (before the real DELETE
  // fires at t=5s) and delta=+1 when the DELETE settles or the user
  // hits Undo. Without this offset the Specs page showed 0 while the
  // sidebar badge showed 1 for the full 5 s window.
  const [pendingSpecDelta, setPendingSpecDelta] = useState(0)
  const [showSwitchPersonalConfirm, setShowSwitchPersonalConfirm] = useState(false)
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ delta: number }>
      const d = ce.detail?.delta ?? 0
      if (d === 0) return
      setPendingSpecDelta((prev) => prev + d)
    }
    window.addEventListener('myos:pending-spec-delta', handler)
    return () => window.removeEventListener('myos:pending-spec-delta', handler)
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
        // Read `count`, not `active_count`. `active_count` only
        // includes sessions that wrote an event in the last 5 minutes,
        // so a Claude Code tab (or torichat tab, or any other session)
        // that has been sitting idle without a tool call briefly falls
        // out and the sidebar lies "No sessions" while the user is
        // clearly still there. `count` includes every session alive
        // inside the 30-minute idle window. Fallback keeps working
        // against older backends that only expose `active_count`.
        const res = await api.get<{ count?: number; active_count?: number }>(
          '/sessions/active',
        )
        setSessionCount(res.count ?? res.active_count ?? 0)
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

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    let consecutiveFailures = 0
    let hasEverBeenHealthy = false
    const SUCCESS_INTERVAL = 15_000
    const FAILURE_INTERVAL = 2_000
    const AMBER_THRESHOLD = 2
    const RED_THRESHOLD = 7

    const scheduleNext = (delayMs: number) => {
      if (cancelled) return
      timer = setTimeout(() => {
        void checkHealth()
      }, delayMs)
    }

    const checkHealth = async () => {
      try {
        const res = await api.get<{ kernel: string }>('/status/clock', { retryOn502: 2, retryDelayMs: 300 })
        if (cancelled) return
        consecutiveFailures = 0
        hasEverBeenHealthy = true
        setHealthState('healthy')
        setLastHealthyAt(Date.now())
        const k = res.kernel || ''
        setOstkKernel(k)
        scheduleNext(SUCCESS_INTERVAL)
      } catch {
        if (cancelled) return
        consecutiveFailures += 1
        if (consecutiveFailures >= RED_THRESHOLD) {
          setHealthState('down')
        } else if (consecutiveFailures >= AMBER_THRESHOLD) {
          // 'starting' = never connected yet (backend warming up after restart).
          // 'checking' = was healthy before, now failing (genuine problem).
          setHealthState(hasEverBeenHealthy ? 'checking' : 'starting')
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

  const dotTooltip = (() => {
    if (healthState === null) return 'Connecting...'
    if (healthState === 'healthy') {
      return `Backend healthy${ostkKernel ? ` (${ostkKernel})` : ''}. Last checked just now.`
    }
    if (healthState === 'starting') {
      return 'Backend starting up...'
    }
    if (healthState === 'checking') {
      return `Backend slow to respond. Checking...${ostkKernel ? ` (${ostkKernel})` : ''}`
    }
    const t = lastHealthyAt ? new Date(lastHealthyAt).toLocaleTimeString() : 'unknown'
    return `Backend not responding. Last healthy at ${t}. To restart: bash scripts/restart-backend.sh`
  })()

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
      .filter((item) => {
        // Gate ostk browser behind power user mode. Route /ostk stays
        // mounted, but the sidebar entry only appears when the user has
        // enabled power user mode in Settings.
        if (item.to === '/ostk' && !powerUserMode) return false
        return true
      })
      .sort((a, b) => {
        if (!a.featureLabel) return -1
        if (!b.featureLabel) return 1
        return (featureOrder.get(a.featureLabel) ?? 999) - (featureOrder.get(b.featureLabel) ?? 999)
      })
  }

  // Top-level items (Home + Tasks + Agents) that are not in any group
  const topLevelItems = ALL_NAV_ITEMS.filter((i) => TOP_LEVEL_ROUTES.has(i.to) && isEnabled(i)).sort((a, b) => {
    const order = ['/', '/tasks', '/specs', '/agents', '/backlog', '/break']
    return order.indexOf(a.to) - order.indexOf(b.to)
  })

  const usageEnabled = isEnabled(USAGE_NAV_ITEM)

  const linkClass = (isActive: boolean) =>
    `group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer active:scale-[0.98] ${
      isActive
        ? 'accent-highlight border-r-2 accent-border'
        : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50'
    }`

  const utilLinkClass = (isActive: boolean) =>
    `group flex items-center gap-3 w-full px-4 py-1.5 rounded-lg transition-colors duration-200 cursor-pointer ${
      isActive
        ? 'text-slate-200 bg-slate-800/40'
        : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800/30'
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

    <aside data-tour="sidebar" style={{ width: sidebarWidth }} className={`h-dvh fixed top-0 ${sidebarPosition === 'right' ? 'right-0 border-l' : 'left-0 border-r'} border-slate-800 bg-slate-950 shadow-2xl flex flex-col py-6 z-50 transition-transform duration-200 ${mobileOpen ? 'translate-x-0' : sidebarPosition === 'right' ? 'translate-x-full' : '-translate-x-full'} lg:translate-x-0`}>
      {/* Resize handle (hidden on mobile) */}
      <div
        onMouseDown={handleSidebarMouseDown}
        className={`hidden lg:block absolute top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-blue-500/30 transition-colors z-10 ${sidebarPosition === 'right' ? 'left-0' : 'right-0'}`}
      />
      <div className="px-5 mb-8">
        <span data-testid="sidebar-os-name" className="text-xl font-black tracking-tight accent-text">{displayOsName}</span>
        {TEAM_MODE_VISIBLE && (
          <span
            data-testid="sidebar-mode-badge"
            className={`ml-2 text-[9px] font-bold px-1.5 py-0.5 rounded-full align-middle ${
              instanceMode === 'team'
                ? 'bg-indigo-500/20 text-indigo-400'
                : 'bg-slate-200 text-slate-700 dark:bg-slate-700/60 dark:text-slate-400'
            }`}
          >
            {instanceMode === 'team' ? 'Team' : 'Personal'}
          </span>
        )}
        {version && (
          <span className="block text-[10px] text-slate-500 font-mono mt-0.5">{version}</span>
        )}
        {displayOsName === 'yourOS' && (
          <span data-testid="sidebar-tagline" className="block text-[10px] italic text-slate-600 mt-0.5">an OS that knows you.</span>
        )}
        {TEAM_MODE_VISIBLE && instanceMode === 'personal' && (
          <Link
            data-testid="team-cta"
            to="/team-setup"
            className="block mt-2 text-[11px] text-slate-400 hover:text-indigo-400 transition-colors"
          >
            Start or join a team →
          </Link>
        )}
      </div>

      <nav className="flex flex-col gap-0.5 px-3 flex-1 overflow-y-auto" data-testid="primary-nav">
        {/* Top-level items: Home, Tasks, Agents */}
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
                {item.specsBadge && Math.max(0, unfinishedSpecs + pendingSpecDelta) > 0 && (
                  <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                    {Math.max(0, unfinishedSpecs + pendingSpecDelta)}
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
              unfinishedSpecs={Math.max(0, unfinishedSpecs + pendingSpecDelta)}
              onNavigate={() => setMobileOpen(false)}
              iconFilled={iconStyle}
              features={features}
              setFeatures={setFeatures}
            />
          )
        })}
      </nav>

      <div className="px-3 pt-3 mt-1 border-t border-slate-800/60 flex flex-col gap-0.5">
        <WhatsNew />
        {TEAM_MODE_VISIBLE && instanceMode === 'team' && enterpriseUser?.role === 'admin' && (
          <AdminSection
            linkClass={linkClass}
            iconStyle={iconStyle}
            onNavigate={() => setMobileOpen(false)}
          />
        )}
        {TEAM_MODE_VISIBLE && instanceMode === 'team' && enterpriseUser && enterpriseUser.role !== 'admin' && (
          <NavLink
            to="/team"
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => linkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name="groups" filled={iconStyle === 'filled' ? true : isActive} className="text-xl" />
                <span className="text-sm font-medium">Team</span>
                <span className="ml-1.5 text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">Beta</span>
              </>
            )}
          </NavLink>
        )}
        {TEAM_MODE_VISIBLE && instanceMode === 'team' && (
          <button
            data-testid="switch-to-personal-mode"
            onClick={() => setShowSwitchPersonalConfirm(true)}
            className="group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer text-slate-500 hover:text-slate-300 hover:bg-slate-800/30"
          >
            <Icon name="person" filled={false} className="text-xl" />
            <span className="text-sm font-medium">Switch to personal</span>
          </button>
        )}
        {usageEnabled && (
          <NavLink
            data-testid="usage-nav-link"
            to={USAGE_NAV_ITEM.to}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => utilLinkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name={USAGE_NAV_ITEM.icon} filled={iconStyle === 'filled' ? true : isActive} className="text-lg" />
                <span className="text-xs font-medium">{USAGE_NAV_ITEM.label}</span>
              </>
            )}
          </NavLink>
        )}
        <NavLink
          to="/settings"
          onClick={() => setMobileOpen(false)}
          className={({ isActive }) => utilLinkClass(isActive)}
        >
          {({ isActive }) => (
            <>
              <Icon name="settings" filled={iconStyle === 'filled' ? true : isActive} className="text-lg" />
              <span className="text-xs font-medium">Settings</span>
            </>
          )}
        </NavLink>
      </div>

      {/* Theme toggle */}
      <div className="px-4 pt-3 pb-2 mt-3 border-t border-slate-300 dark:border-slate-800" data-testid="theme-toggle-container">
        <button
          data-testid="theme-toggle"
          aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
          onClick={toggleDarkMode}
          className="relative flex items-center w-full h-10 rounded-full bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 transition-colors duration-200 cursor-pointer border border-slate-300 dark:border-slate-700 p-1"
        >
          {/* Sun icon (light) */}
          <span className={`w-1/2 flex items-center justify-center z-10 transition-colors duration-200 ${!darkMode ? 'text-amber-500' : 'text-slate-500 hover:text-slate-400'}`}>
            <Icon name="light_mode" size={20} />
          </span>
          {/* Moon icon (dark) */}
          <span className={`w-1/2 flex items-center justify-center z-10 transition-colors duration-200 ${darkMode ? 'text-blue-400' : 'text-slate-400 hover:text-slate-500'}`}>
            <Icon name="dark_mode" size={20} />
          </span>
          {/* Sliding indicator */}
          <span
            data-testid="theme-toggle-indicator"
            className={`absolute top-1 bottom-1 left-1 w-[calc(50%-4px)] rounded-full bg-white border border-slate-300 shadow-sm dark:bg-slate-600 dark:border-transparent dark:shadow-none transition-transform duration-200 ${darkMode ? 'translate-x-[100%]' : 'translate-x-0'}`}
          />
        </button>
      </div>

      {/* Backend status indicator */}
      <div className="px-5 pt-3 pb-2 border-t border-slate-800/50 flex flex-col gap-1.5">
        {statusDotStyle === 'badges' ? (
          <span
            data-testid="backend-status-dot"
            title={dotTooltip}
            className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${
              healthState === null
                ? 'bg-slate-700 text-slate-400'
                : healthState === 'healthy'
                ? 'bg-green-500/20 text-green-400'
                : healthState === 'starting'
                ? 'bg-slate-700 text-slate-400 animate-pulse'
                : healthState === 'checking'
                ? 'bg-amber-500/20 text-amber-400'
                : 'bg-red-500/20 text-red-400'
            }`}
          >
            Backend {healthState === null ? '' : healthState === 'healthy' ? 'up' : healthState === 'starting' ? 'starting up' : healthState === 'checking' ? 'checking' : 'down'}
          </span>
        ) : (
          <div className="flex items-center gap-2">
            <span
              data-testid="backend-status-dot"
              title={dotTooltip}
              className={`w-1.5 h-1.5 rounded-full ${
                healthState === null
                  ? 'bg-slate-600'
                  : healthState === 'healthy'
                  ? 'bg-green-400'
                  : healthState === 'starting'
                  ? 'bg-slate-400 animate-pulse'
                  : healthState === 'checking'
                  ? 'bg-amber-400'
                  : 'bg-red-400'
              }`}
            />
            <span className="text-[10px] text-slate-500">Backend</span>
          </div>
        )}
      </div>
    </aside>
    <ConfirmModal
      open={showSwitchPersonalConfirm}
      title="Switch to personal mode?"
      message="Your team data stays saved."
      confirmLabel="Switch"
      onConfirm={() => {
        setShowSwitchPersonalConfirm(false)
        useAppStore.getState().setInstanceMode('personal')
      }}
      onCancel={() => setShowSwitchPersonalConfirm(false)}
    />
    </>
  )
}
