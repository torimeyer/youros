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
import { useAppStore, useTerms } from '../stores/app'
import { api } from '../lib/api'

interface NavItem {
  to: string
  icon: string
  label: string
  featureLabel: string | null
  badge?: boolean
  gmailBadge?: boolean
}

function SortableNavItem({ item, linkClass, activeAgents, gmailUnread }: {
  item: NavItem
  linkClass: (isActive: boolean) => string
  activeAgents: number
  gmailUnread: number
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
        className={({ isActive }) => linkClass(isActive)}
      >
        {({ isActive }) => (
          <>
            <Icon name={item.icon} filled={isActive} className="text-xl" />
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
  const osName = useAppStore((s) => s.osName)
  const features = useAppStore((s) => s.features)
  const setFeatures = useAppStore((s) => s.setFeatures)
  const t = useTerms()

  const allNavItems = [
    { to: '/', icon: 'home', label: 'Home', featureLabel: null },
    { to: '/tasks', icon: 'checklist', label: t('tasks'), featureLabel: 'Tasks' },
    { to: '/activity', icon: 'history', label: 'Activity', featureLabel: 'Activity' },
    { to: '/ideas', icon: 'lightbulb', label: t('ideas'), featureLabel: 'Hay/Ideas' },
    { to: '/agents', icon: 'smart_toy', label: 'Agents', badge: true, featureLabel: 'Agents' },
    { to: '/files', icon: 'folder', label: 'Files', featureLabel: 'Projects' },
    { to: '/drive', icon: 'cloud', label: 'Drive', featureLabel: 'Drive' },
    { to: '/calendar', icon: 'calendar_month', label: 'Calendar', featureLabel: 'Calendar' },
    { to: '/gmail', icon: 'mail', label: 'Gmail', featureLabel: 'Gmail', gmailBadge: true },
    { to: '/transcripts', icon: 'history', label: 'History', featureLabel: 'Transcripts' },
    { to: '/workflows', icon: 'account_tree', label: 'Automations', featureLabel: 'Automations' },
  ]
  const [activeAgents, setActiveAgents] = useState(0)
  const [gmailUnread, setGmailUnread] = useState(0)
  const [version, setVersion] = useState('')
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [ostkUp, setOstkUp] = useState<boolean | null>(null)
  const [ostkKernel, setOstkKernel] = useState('')

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
    api.get<{ myos: { current: string } }>('/upgrade/status')
      .then((res) => setVersion(res.myos?.current ?? ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await api.get<{ kernel: string }>('/status/clock')
        setBackendUp(true)
        const k = res.kernel || ''
        setOstkUp(k !== 'unknown' && k !== '')
        setOstkKernel(k)
      } catch {
        setBackendUp(false)
        setOstkUp(false)
        setOstkKernel('')
      }
    }
    checkHealth()
    const interval = setInterval(checkHealth, 15000)
    return () => clearInterval(interval)
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
    <aside data-tour="sidebar" className="h-screen w-56 fixed left-0 top-0 border-r border-slate-800 bg-slate-950 shadow-2xl flex flex-col py-6 z-50">
      <div className="px-5 mb-8">
        <span className="text-xl font-black accent-text tracking-tight">{osName}</span>
        {version && (
          <span className="block text-[10px] text-slate-500 font-mono mt-0.5">{version}</span>
        )}
      </div>

      <nav className="flex flex-col gap-1 px-3 flex-1">
        {/* Home is always first and not draggable */}
        {navItems.filter((item) => !item.featureLabel).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => linkClass(isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon name={item.icon} filled={isActive} className="text-xl" />
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
              />
            ))}
          </SortableContext>
        </DndContext>
      </nav>

      <div className="px-3 flex flex-col gap-1">
        <WhatsNew />
        <button
          data-testid="tour-button"
          onClick={() => useAppStore.getState().setShowTour(true)}
          className="group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer text-slate-400 hover:text-slate-100 hover:bg-slate-800/50"
        >
          <Icon name="explore" className="text-xl" />
          <span className="text-sm font-medium">Tour</span>
        </button>
        <NavLink
          to="/settings"
          className={({ isActive }) => linkClass(isActive)}
        >
          {({ isActive }) => (
            <>
              <Icon name="settings" filled={isActive} className="text-xl" />
              <span className="text-sm font-medium">Settings</span>
            </>
          )}
        </NavLink>
      </div>

      {/* System status indicators */}
      <div className="px-5 pt-3 pb-2 border-t border-slate-800/50 flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full ${backendUp === null ? 'bg-slate-600' : backendUp ? 'bg-green-400' : 'bg-red-400'}`} />
          <span className="text-[10px] text-slate-500">Backend</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full ${ostkUp === null ? 'bg-slate-600' : ostkUp ? 'bg-green-400' : 'bg-red-400'}`} />
          <span className="text-[10px] text-slate-500">ostk{ostkKernel ? ` ${ostkKernel}` : ''}</span>
        </div>
      </div>
    </aside>
  )
}
