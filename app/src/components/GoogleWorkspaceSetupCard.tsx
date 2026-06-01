import { useState, useEffect } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'

interface DriveAuthStatus {
  authenticated: boolean
  email: string | null
}

function googleAccountLabel(email: string | null): string {
  if (!email) return 'Google account'
  const lower = email.toLowerCase()
  if (lower.endsWith('@gmail.com') || lower.endsWith('@googlemail.com')) {
    return 'Google account'
  }
  return 'Google Workspace'
}

export function GoogleAccountSetupCard({
  darkMode,
  subtextCls,
  stepIndex,
}: {
  darkMode: boolean
  subtextCls: string
  stepIndex?: number
}) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [email, setEmail] = useState<string | null>(null)

  const fetchStatus = () => {
    api.get<DriveAuthStatus>('/drive/auth/status')
      .then((data) => {
        setConnected(Boolean(data.authenticated))
        setEmail(data.email ?? null)
      })
      .catch(() => setConnected(false))
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('connected') === 'true' || params.get('auth_success') === 'true') {
      window.history.replaceState({}, '', '/')
    }
    fetchStatus()
  }, [])

  const cardBase = `mt-2 p-3 rounded-lg border ${darkMode ? 'border-slate-200 dark:border-slate-700' : 'border-gray-200'}`

  if (connected === null) {
    return (
      <div className={cardBase} data-testid="onboarding-google-workspace-card">
        <div className="flex items-center gap-2 text-sm">
          <div className="w-2.5 h-2.5 rounded-full bg-slate-600 animate-pulse" />
          <span className={subtextCls}>Checking Google account...</span>
        </div>
      </div>
    )
  }

  if (connected) {
    return (
      <div className={cardBase} data-testid="onboarding-google-workspace-card">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium">{googleAccountLabel(email)}</p>
              {email && <p className={`text-xs ${subtextCls}`}>{email}</p>}
            </div>
          </div>
          <span
            className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400"
            data-testid="google-workspace-connected-pill"
          >
            Connected
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className={cardBase} data-testid="onboarding-google-workspace-card">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{googleAccountLabel(null)} (Drive, Calendar, Gmail)</p>
        <button
          onClick={() => {
            if (stepIndex !== undefined) api.patch('/settings', { onboarding_step: stepIndex }).catch(() => {})
            api.get<{ url: string }>('/drive/auth/url?return_to=%2F')
              .then((res) => { window.location.href = res.url })
              .catch((e) => reportError('Google account OAuth failed to start', e))
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-xs font-medium transition-colors ${
            darkMode
              ? 'bg-slate-100 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:border-blue-500'
              : 'bg-gray-50 border-gray-200 text-slate-600 hover:border-blue-500'
          }`}
          data-testid="connect-google-workspace"
        >
          <Icon name="folder_shared" size={14} />
          Connect
        </button>
      </div>
      <p className={`text-xs mt-1.5 ${subtextCls}`}>
        One click. Google will ask for permission, then bring you back here.
      </p>
    </div>
  )
}

export { GoogleAccountSetupCard as GoogleWorkspaceSetupCard }
