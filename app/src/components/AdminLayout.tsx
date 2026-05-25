import { NavLink, Outlet } from 'react-router-dom'
import Icon from './Icon'
import TopBar from './TopBar'
import { useAppStore } from '../stores/app'

const ADMIN_NAV = [
  { to: '/admin', label: 'Overview', icon: 'dashboard', end: true },
  { to: '/admin/members', label: 'Members', icon: 'group' },
  { to: '/admin/policies', label: 'Policies', icon: 'shield' },
  { to: '/admin/audit', label: 'Audit Trail', icon: 'history' },
  { to: '/admin/security', label: 'Security', icon: 'lock' },
]

export default function AdminLayout() {
  const displayOsName = useAppStore((s) => s.displayOsName())
  const darkMode = useAppStore((s) => s.darkMode)

  return (
    <div className="flex-1 flex flex-col">
      <TopBar title={`${displayOsName} Admin`} />
      <div className="flex-1 overflow-y-auto">
        {/* Team accent header stripe */}
        <div className="team-bg h-1 w-full" />
        <div className="max-w-5xl mx-auto w-full px-6 py-6">
          {/* Admin sub-nav */}
          <nav className="flex gap-1 mb-6 overflow-x-auto pb-1">
            {ADMIN_NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
                    isActive
                      ? darkMode
                        ? 'bg-indigo-500/20 text-indigo-700 dark:text-indigo-300'
                        : 'bg-indigo-50 text-indigo-700'
                      : darkMode
                        ? 'text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/50'
                        : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100'
                  }`
                }
              >
                <Icon name={item.icon} size={18} />
                {item.label}
              </NavLink>
            ))}
          </nav>
          <Outlet />
        </div>
      </div>
    </div>
  )
}
