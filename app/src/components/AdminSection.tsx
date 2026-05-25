import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import Icon from './Icon'

const ADMIN_LINKS = [
  { to: '/admin', label: 'Overview', icon: 'dashboard', end: true },
  { to: '/admin/members', label: 'Members', icon: 'group' },
  { to: '/admin/policies', label: 'Policies', icon: 'shield' },
  { to: '/admin/audit', label: 'Audit Trail', icon: 'history' },
  { to: '/admin/security', label: 'Security', icon: 'lock' },
]

export default function AdminSection({
  linkClass,
  iconStyle,
  onNavigate,
}: {
  linkClass: (isActive: boolean) => string
  iconStyle: string
  onNavigate?: () => void
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="group flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800/50"
      >
        <Icon name="admin_panel_settings" filled={iconStyle === 'filled'} className="text-xl" />
        <span className="text-sm font-medium">Admin</span>
        <Icon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={16}
          className="ml-auto text-slate-500"
        />
      </button>
      {expanded && (
        <div className="ml-3 mt-0.5 space-y-0.5">
          {ADMIN_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              onClick={onNavigate}
              className={({ isActive }) => linkClass(isActive)}
            >
              {({ isActive }) => (
                <>
                  <Icon name={link.icon} filled={iconStyle === 'filled' ? true : isActive} className="text-lg" />
                  <span className="text-xs font-medium">{link.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}
