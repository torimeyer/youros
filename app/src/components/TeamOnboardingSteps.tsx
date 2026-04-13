import { useState } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

/* ---- Shared types ---- */

export interface TeamOnboardingData {
  orgName: string
  adminEmail: string
  inviteEmails: string[]
  isolationLevel: 'open' | 'governed' | 'sealed'
}

/* ---- OrgName step ---- */

export function OrgNameStep({
  orgName,
  setOrgName,
  inputCls,
  subtextCls,
}: {
  orgName: string
  setOrgName: (v: string) => void
  inputCls: string
  subtextCls: string
}) {
  return (
    <div data-testid="step-org-name">
      <h2 className="text-2xl font-bold mb-2">Name your team workspace</h2>
      <p className={`${subtextCls} mb-2`}>
        This becomes your team's identity. If your team is called Acme, you'll see "Acme OS" in the sidebar.
      </p>
      <input
        type="text"
        value={orgName}
        onChange={(e) => setOrgName(e.target.value)}
        placeholder="e.g. Acme, Engineering, Product"
        className={`w-full border rounded-lg px-4 py-3 text-lg focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
        data-testid="org-name-input"
        autoFocus
      />
      {orgName && (
        <p className="mt-4 text-sm text-blue-400">
          Your team workspace will be called <span className="font-bold">{orgName} OS</span>
        </p>
      )}
    </div>
  )
}

/* ---- AdminEmail step ---- */

export function AdminEmailStep({
  adminEmail,
  setAdminEmail,
  inputCls,
  subtextCls,
}: {
  adminEmail: string
  setAdminEmail: (v: string) => void
  inputCls: string
  subtextCls: string
}) {
  return (
    <div data-testid="step-admin-email">
      <h2 className="text-2xl font-bold mb-2">Your email</h2>
      <p className={`${subtextCls} mb-6`}>
        You'll be the admin. Team members will use this to know who manages the workspace.
      </p>
      <input
        type="email"
        value={adminEmail}
        onChange={(e) => setAdminEmail(e.target.value)}
        placeholder="you@company.com"
        className={`w-full border rounded-lg px-4 py-3 text-lg focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
        data-testid="admin-email-input"
        autoFocus
      />
    </div>
  )
}

/* ---- InviteTeam step ---- */

export function InviteTeamStep({
  inviteEmails,
  setInviteEmails,
  inputCls,
  subtextCls,
  darkMode,
}: {
  inviteEmails: string[]
  setInviteEmails: (v: string[]) => void
  inputCls: string
  subtextCls: string
  darkMode: boolean
}) {
  const [currentEmail, setCurrentEmail] = useState('')

  const addEmail = () => {
    const trimmed = currentEmail.trim()
    if (trimmed && trimmed.includes('@') && !inviteEmails.includes(trimmed)) {
      setInviteEmails([...inviteEmails, trimmed])
      setCurrentEmail('')
    }
  }

  const removeEmail = (email: string) => {
    setInviteEmails(inviteEmails.filter((e) => e !== email))
  }

  return (
    <div data-testid="step-invite-team">
      <h2 className="text-2xl font-bold mb-2">Add your team</h2>
      <p className={`${subtextCls} mb-6`}>
        Enter email addresses for people you want to invite. You can always invite more people later.
      </p>
      <div className="flex gap-2 mb-4">
        <input
          type="email"
          value={currentEmail}
          onChange={(e) => setCurrentEmail(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addEmail() } }}
          placeholder="teammate@company.com"
          className={`flex-1 border rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="invite-email-input"
          autoFocus
        />
        <button
          onClick={addEmail}
          disabled={!currentEmail.trim() || !currentEmail.includes('@')}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:hover:bg-blue-600 rounded-lg text-sm font-medium text-white transition-colors"
        >
          Add
        </button>
      </div>
      {inviteEmails.length > 0 && (
        <div className="space-y-2">
          {inviteEmails.map((email) => (
            <div key={email} className={`flex items-center justify-between px-3 py-2 rounded-lg ${darkMode ? 'bg-slate-800/50' : 'bg-gray-100'}`}>
              <span className={`text-sm ${darkMode ? 'text-slate-300' : 'text-slate-700'}`}>{email}</span>
              <button onClick={() => removeEmail(email)} className="text-slate-500 hover:text-red-400 transition-colors">
                <Icon name="close" size={16} />
              </button>
            </div>
          ))}
          <p className="text-xs text-slate-500 mt-2">{inviteEmails.length} {inviteEmails.length === 1 ? 'person' : 'people'} will be invited</p>
        </div>
      )}
      {inviteEmails.length === 0 && (
        <p className={`text-sm ${subtextCls}`}>No one added yet. You can skip this and invite people later.</p>
      )}
    </div>
  )
}

/* ---- Guardrails step ---- */

const ISOLATION_LEVELS = [
  {
    id: 'open' as const,
    label: 'Open',
    icon: 'lock_open',
    description: 'Agents can read, write, and run commands. Best for trusted teams that want full autonomy.',
  },
  {
    id: 'governed' as const,
    label: 'Governed',
    icon: 'shield',
    description: 'Agents can read files and search the web, but need approval for writes and commands. Good balance of safety and productivity.',
  },
  {
    id: 'sealed' as const,
    label: 'Sealed',
    icon: 'lock',
    description: 'Agents can only read files and search. No writes, no commands. Maximum control for sensitive environments.',
  },
]

export function GuardrailsStep({
  isolationLevel,
  setIsolationLevel,
  subtextCls,
  darkMode,
}: {
  isolationLevel: 'open' | 'governed' | 'sealed'
  setIsolationLevel: (v: 'open' | 'governed' | 'sealed') => void
  subtextCls: string
  darkMode: boolean
}) {
  const unselectedCard = darkMode
    ? 'bg-slate-900 border-slate-800 hover:border-slate-700'
    : 'bg-white border-gray-200 hover:border-gray-300 shadow-sm'
  const unselectedIcon = darkMode
    ? 'bg-slate-800 text-slate-400'
    : 'bg-gray-100 text-slate-500'
  const labelCls = darkMode ? 'text-white' : 'text-slate-900'

  return (
    <div data-testid="step-guardrails">
      <h2 className="text-2xl font-bold mb-2">Set guardrails</h2>
      <p className={`${subtextCls} mb-6`}>
        Choose how much freedom agents have. This applies to all team members. You can change this later in Admin settings.
      </p>
      <div className="space-y-3">
        {ISOLATION_LEVELS.map((level) => (
          <button
            key={level.id}
            onClick={() => setIsolationLevel(level.id)}
            className={`w-full flex items-start gap-4 p-4 rounded-xl border text-left transition-colors ${
              isolationLevel === level.id
                ? 'bg-blue-500/20 border-blue-500'
                : unselectedCard
            }`}
          >
            <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
              isolationLevel === level.id ? 'bg-blue-500/30 text-blue-300' : unselectedIcon
            }`}>
              <Icon name={level.icon} size={22} />
            </div>
            <div className="flex-1 min-w-0">
              <p className={`font-medium ${labelCls}`}>{level.label}</p>
              <p className={`text-sm mt-0.5 ${subtextCls}`}>{level.description}</p>
            </div>
            {isolationLevel === level.id && <Icon name="check_circle" className="text-blue-400 mt-1" size={20} />}
          </button>
        ))}
      </div>
    </div>
  )
}

/* ---- TeamReady step ---- */

export function TeamReadyStep({
  orgName,
  adminEmail,
  inviteCount,
  isolationLevel,
  subtextCls,
  cardCls,
}: {
  orgName: string
  adminEmail: string
  inviteCount: number
  isolationLevel: string
  subtextCls: string
  cardCls: string
}) {
  const levelLabel = ISOLATION_LEVELS.find((l) => l.id === isolationLevel)?.label || isolationLevel
  return (
    <div className="text-center" data-testid="step-team-ready">
      <div className="mb-6">
        <Icon name="groups" size={48} className="text-indigo-400" />
      </div>
      <h1 className="text-3xl font-bold mb-2">{orgName} OS is ready</h1>
      <p className={`${subtextCls} mb-8`}>Here's what we set up for your team.</p>
      <div className={`rounded-xl p-6 border ${cardCls} text-left space-y-3`}>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>Workspace</span>
          <span className="text-sm font-medium">{orgName} OS</span>
        </div>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>Admin</span>
          <span className="text-sm font-medium">{adminEmail}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>Team members</span>
          <span className="text-sm font-medium">{inviteCount} invited</span>
        </div>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>Guardrails</span>
          <span className="text-sm font-medium">{levelLabel}</span>
        </div>
      </div>
    </div>
  )
}

/* ---- Finish handler ---- */

export async function finishTeamOnboarding(data: TeamOnboardingData): Promise<boolean> {
  try {
    // 1. Create org
    await api.post('/enterprise/org', { name: data.orgName, admin_email: data.adminEmail })

    // 2. Invite team members
    for (const email of data.inviteEmails) {
      try {
        await api.post('/enterprise/members', { email, role: 'member' })
      } catch {
        // Skip duplicates or failures
      }
    }

    // 3. Set isolation level / policies
    await api.patch('/enterprise/policies', { isolation_level: data.isolationLevel })

    return true
  } catch {
    return false
  }
}
