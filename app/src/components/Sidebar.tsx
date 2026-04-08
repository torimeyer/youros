import { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore, useTerms } from '../stores/app'
import { api } from '../lib/api'

export function Sidebar() {
  const osName = useAppStore((s) => s.osName)
  const features = useAppStore((s) => s.features)
  const t = useTerms()

  const allNavItems = [
    { to: '/', icon: 'home', label: 'Home', featureLabel: null },
    { to: '/tasks', icon: 'checklist', label: t('tasks'), featureLabel: 'Tasks' },
    { to: '/activity', icon: 'history', label: 'Activity', featureLabel: null },
    { to: '/ideas', icon: 'lightbulb', label: t('ideas'), featureLabel: 'Hay/Ideas' },
    { to: '/agents', icon: 'smart_toy', label: 'Agents', badge: true, featureLabel: 'Agents' },
    { to: '/files', icon: 'folder', label: 'Files', featureLabel: 'Projects' },
    { to: '/drive', icon: 'cloud', label: 'Drive', featureLabel: null },
    { to: '/calendar', icon: 'calendar_month', label: 'Calendar', featureLabel: null },
    { to: '/gmail', icon: 'mail', label: 'Gmail', featureLabel: null, gmailBadge: true },
    { to: '/transcripts', icon: 'record_voice_over', label: 'Transcripts', featureLabel: 'Transcripts' },
    { to: '/workflows', icon: 'account_tree', label: 'Workflows', featureLabel: null },
  ]
  const [activeAgents, setActiveAgents] = useState(0)
  const [gmailUnread, setGmailUnread] = useState(0)

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const res = await api.get<{ active: string[] }>('/agents')
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

  // Filter nav items based on feature toggles
  const navItems = allNavItems.filter((item) => {
    if (!item.featureLabel) return true
    const feature = features.find((f) => f.label === item.featureLabel)
    return feature ? feature.enabled : true
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
      </div>

      <nav className="flex flex-col gap-1 px-3 flex-1">
        {navItems.map((item) => (
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
                {'badge' in item && item.badge && activeAgents > 0 && (
                  <span className="ml-auto flex items-center gap-1 bg-green-500/20 text-green-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                    {activeAgents}
                  </span>
                )}
                {'gmailBadge' in item && item.gmailBadge && gmailUnread > 0 && (
                  <span className="ml-auto bg-red-500/20 text-red-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                    {gmailUnread}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="px-3 flex flex-col gap-1">
        <button
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
    </aside>
  )
}
