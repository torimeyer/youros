import { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
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

interface NavItem {
  to: string
  icon: string
  label: string
  featureLabel: string | null
  badge?: boolean
  gmailBadge?: boolean
}

function SortableNavItem({ item, linkClass, activeAgents, gmailUnread, onNavigate, iconFilled }: {
  item: NavItem
  linkClass: (isActive: boolean) => string
  activeAgents: number
  gmailUnread: number
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
          </>
        )}
      </NavLink>
    </div>
  )
}

export function Sidebar() {
  const displayOsName = useAppStore((s) => s.displayOsName())
  const instanceMode = useAppStore((s) => s.instanceMode)
  const features = useAppStore((s) => s.features)
  const setFeatures = useAppStore((s) => s.setFeatures)
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)
  const sidebarPosition = useAppStore((s) => s.sidebarPosition)
  const iconStyle = useAppStore((s) => s.iconStyle)
  const statusDotStyle = useAppStore((s) => s.statusDotStyle)
  const [mobileOpen, setMobileOpen] = useState(false)

  const allNavItems = [
    { to: '/', icon: 'home', label: 'Home', featureLabel: null },
    { to: '/tasks', icon: 'checklist', label: 'Tasks', featureLabel: 'Tasks' },
    { to: '/ideas', icon: 'lightbulb', label: 'Ideas', featureLabel: 'Ideas' },
    { to: '/agents', icon: 'smart_toy', label: 'Agents', badge: true, featureLabel: 'Agents' },
    { to: '/activity', icon: 'history', label: 'Activity', featureLabel: 'Activity' },
    { to: '/files', icon: 'folder', label: 'Files', featureLabel: 'Projects' },
    { to: '/drive', icon: 'cloud', label: 'Drive', featureLabel: 'Drive' },
    { to: '/calendar', icon: 'calendar_month', label: 'Calendar', featureLabel: 'Calendar' },
    { to: '/gmail', icon: 'mail', label: 'Gmail', featureLabel: 'Gmail', gmailBadge: true },
    { to: '/imessage', icon: 'chat_bubble', label: 'iMessage', featureLabel: 'iMessage' },
    { to: '/slack', icon: 'chat', label: 'Slack', featureLabel: 'Slack' },
    { to: '/github', icon: 'code', label: 'GitHub', featureLabel: 'GitHub' },
    { to: '/costs', icon: 'payments', label: 'Cost Tracking', featureLabel: 'Cost Tracking' },
    { to: '/docs', icon: 'description', label: 'Docs', featureLabel: 'Docs' },
    { to: '/workflows', icon: 'account_tree', label: 'Automations', featureLabel: 'Automations' },
  ]
  const [activeAgents, setActiveAgents] = useState(0)
  const [gmailUnread, setGmailUnread] = useState(0)
  const [version, setVersion] = useState('')
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [ostkUp, setOstkUp] = useState<boolean | null>(null)
  const [ostkKernel, setOstkKernel] = useState('')
  const [sessionCount, setSessionCount] = useState(0)

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        interface AgentInfo { name: string; status: string; spawned_at?: string; completed_at?: string }
        const res = await api.get<{ active: string[]; agents: AgentInfo[] }>('/agents')
        setActiveAgents(res.active?.length ?? 0)
      } catch {
        // ignore
      }
    }
    fetchAgents()
    const interval = setInterval(fetchAgents, 5000)
    return () => clearInterval(interval)
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

  useEffect(() => {
    // Adaptive poll interval with failure debouncing.
    //
    // History:
    //   Needle 286: a single failed poll during a restart window used
    //     to pin both dots red for a full 15 second interval. Fixed by
    //     shortening the retry interval to 2 seconds on failure.
    //   Needle 287: a backend restart stranded keep alive sockets in
    //     the vite proxy pool, so every /api/* request hung for 30s.
    //     Fixed by forcing Connection: close in vite.config.ts.
    //   Needle 293: even with the 2s retry, a single failed poll still
    //     flipped the dot red for up to 2 seconds during a fast
    //     restart. Tori asks for zero red frames when the servers come
    //     back in under 3 seconds. Fix: require two consecutive
    //     failures before flipping the dot red. A single transient
    //     failure just schedules a faster retry and keeps the previous
    //     state. With FAILURE_INTERVAL at 2s and FAILURE_THRESHOLD at
    //     2, a genuinely down backend is reported red within 2 seconds
    //     (first fail at t=0 is tolerated, second fail at t=2 flips
    //     red), which is comfortably under the 5 second bar.
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
        // Keep retrying on the short failure interval until we either
        // recover (consecutive resets to 0) or confirm the backend is
        // really gone (already red).
        scheduleNext(FAILURE_INTERVAL)
      }
    }
    void checkHealth()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  // Filter and sort nav items based on feature toggles.
  // Home is always first, then items follow the order in the features array.
  const featureOrder = new Map(features.map((f, i) => [f.label, i]))
  const navItems = allNavItems
    .filter((item) => {
      if (!item.featureLabel) return true
      const feature = features.find((f) => f.label === item.featureLabel)
      return feature ? feature.enabled : true
    })
    .sort((a, b) => {
      if (!a.featureLabel) return -1
      if (!b.featureLabel) return 1
      const orderA = featureOrder.get(a.featureLabel) ?? 999
      const orderB = featureOrder.get(b.featureLabel) ?? 999
      return orderA - orderB
    })

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

      <nav className="flex flex-col gap-1 px-3 flex-1 overflow-y-auto">
        {/* Home is always first and not draggable */}
        {navItems.filter((item) => !item.featureLabel).map((item) => (
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
              </>
            )}
          </NavLink>
        ))}
        {/* Draggable nav items */}
        <DndContext
          sensors={useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))}
          collisionDetection={closestCenter}
          onDragEnd={(event: DragEndEvent) => {
            const { active, over } = event
            if (!over || active.id === over.id) return
            const draggableItems = navItems.filter((item) => item.featureLabel)
            const oldIndex = draggableItems.findIndex((item) => item.to === active.id)
            const newIndex = draggableItems.findIndex((item) => item.to === over.id)
            if (oldIndex === -1 || newIndex === -1) return
            const reordered = arrayMove(draggableItems, oldIndex, newIndex)
            // Update features array to match new order
            const newFeatures = reordered
              .map((item) => features.find((f) => f.label === item.featureLabel))
              .filter((f): f is { label: string; enabled: boolean } => f != null)
            // Add any features not in the nav (like Chat, Docs)
            const seen = new Set(newFeatures.map((f) => f.label))
            for (const f of features) {
              if (!seen.has(f.label)) newFeatures.push(f)
            }
            setFeatures(newFeatures)
          }}
        >
          <SortableContext
            items={navItems.filter((item) => item.featureLabel).map((item) => item.to)}
            strategy={verticalListSortingStrategy}
          >
            {navItems.filter((item) => item.featureLabel).map((item) => (
              <SortableNavItem
                key={item.to}
                item={item}
                linkClass={linkClass}
                activeAgents={activeAgents}
                gmailUnread={gmailUnread}
                onNavigate={() => setMobileOpen(false)}
                iconFilled={iconStyle}
              />
            ))}
          </SortableContext>
        </DndContext>
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
