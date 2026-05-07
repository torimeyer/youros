import { useState, useEffect } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

interface SlackStatus {
  connected: boolean
  team_name: string
  team_id: string
  configured: boolean
}

export default function SlackConnect() {
  const [status, setStatus] = useState<SlackStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [disconnecting, setDisconnecting] = useState(false)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [configuring, setConfiguring] = useState(false)
  const [configureError, setConfigureError] = useState<string | null>(null)
  const [showCredForm, setShowCredForm] = useState(false)

  useEffect(() => {
    api.get<SlackStatus>('/slack/status')
      .then(setStatus)
      .catch(() => setStatus({ connected: false, team_name: '', team_id: '', configured: false }))
      .finally(() => setLoading(false))
  }, [])

  const handleConnect = () => {
    window.location.href = '/api/auth/slack/login'
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.delete('/slack/disconnect')
      setStatus({ connected: false, team_name: '', team_id: '', configured: false })
    } catch {
      // ignore
    } finally {
      setDisconnecting(false)
    }
  }

  const handleConfigure = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!clientId.trim() || !clientSecret.trim()) return
    setConfiguring(true)
    setConfigureError(null)
    try {
      await api.post('/slack/configure', { client_id: clientId.trim(), client_secret: clientSecret.trim() })
      const updated = await api.get<SlackStatus>('/slack/status')
      setStatus(updated)
      setShowCredForm(false)
    } catch {
      setConfigureError('Could not save credentials. Check that they are correct and try again.')
    } finally {
      setConfiguring(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <div className="w-2.5 h-2.5 rounded-full bg-slate-600 animate-pulse" />
        Checking Slack...
      </div>
    )
  }

  if (status?.connected) {
    return (
      <div className="flex items-center gap-3" data-testid="slack-connect-connected">
        <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm text-slate-200 font-medium">
            Connected to {status.team_name || 'your workspace'}
          </p>
          <p className="text-xs text-slate-500">Slack workspace</p>
        </div>
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          data-testid="slack-disconnect-btn"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
        >
          <Icon name="link_off" size={15} />
          Disconnect
        </button>
      </div>
    )
  }

  const configured = status?.configured ?? false

  if (configured) {
    return (
      <div data-testid="slack-connect-disconnected">
        <button
          onClick={handleConnect}
          data-testid="slack-connect-btn"
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium text-white transition-colors"
        >
          <Icon name="forum" size={16} />
          Connect your Slack workspace
        </button>
        <p className="text-xs text-slate-500 mt-1.5">One-click sign in via Slack OAuth</p>
        <button
          onClick={() => setShowCredForm(true)}
          className="text-xs text-slate-500 hover:text-slate-300 mt-1 underline"
        >
          Change credentials
        </button>
        {showCredForm && (
          <form onSubmit={handleConfigure} className="mt-3 space-y-2">
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Client ID"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500/50"
            />
            <input
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="Client Secret"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500/50"
            />
            {configureError && <p className="text-xs text-red-400">{configureError}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={configuring || !clientId.trim() || !clientSecret.trim()}
                className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
              >
                {configuring ? 'Saving...' : 'Save'}
              </button>
              <button
                type="button"
                onClick={() => setShowCredForm(false)}
                className="px-3 py-1.5 text-sm text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    )
  }

  return (
    <div data-testid="slack-connect-disconnected">
      <p className="text-sm text-slate-400 mb-3">
        Enter your Slack app credentials to enable sign-in.{' '}
        <a
          href="https://api.slack.com/apps"
          target="_blank"
          rel="noreferrer"
          className="text-purple-400 hover:text-purple-300 underline"
        >
          Create an app at api.slack.com/apps
        </a>
      </p>
      <form onSubmit={handleConfigure} className="space-y-2">
        <input
          type="text"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          placeholder="Client ID"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500/50"
          data-testid="slack-client-id-input"
        />
        <input
          type="password"
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
          placeholder="Client Secret"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500/50"
          data-testid="slack-client-secret-input"
        />
        {configureError && <p className="text-xs text-red-400">{configureError}</p>}
        <button
          type="submit"
          disabled={configuring || !clientId.trim() || !clientSecret.trim()}
          data-testid="slack-connect-btn"
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
        >
          <Icon name="forum" size={16} />
          {configuring ? 'Saving...' : 'Save and connect'}
        </button>
      </form>
    </div>
  )
}
