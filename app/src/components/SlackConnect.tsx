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
  const [accessToken, setAccessToken] = useState('')
  const [appId, setAppId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

  useEffect(() => {
    api.get<SlackStatus>('/slack/status')
      .then(setStatus)
      .catch(() => setStatus({ connected: false, team_name: '', team_id: '', configured: false }))
      .finally(() => setLoading(false))
  }, [])

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

  const handleConnectToken = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!accessToken.trim()) return
    setConnecting(true)
    setConnectError(null)
    try {
      await api.post('/slack/connect-token', { access_token: accessToken.trim(), app_id: appId.trim() })
      const updated = await api.get<SlackStatus>('/slack/status')
      setStatus(updated)
      setAccessToken('')
      setAppId('')
    } catch (err) {
      const detail = (err as { detail?: string })?.detail
      setConnectError(detail || 'Slack would not accept that token. Check it and try again.')
    } finally {
      setConnecting(false)
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
          <p className="text-sm text-slate-800 dark:text-slate-200 font-medium">
            Connected to {status.team_name || 'your workspace'}
          </p>
          <p className="text-xs text-slate-500">Slack workspace</p>
        </div>
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          data-testid="slack-disconnect-btn"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-sm text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors disabled:opacity-50"
        >
          <Icon name="link_off" size={15} />
          Disconnect
        </button>
      </div>
    )
  }

  return (
    <div data-testid="slack-connect-disconnected">
      <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
        Paste your Slack Access Token and App ID to connect.{' '}
        <a
          href="https://api.slack.com/apps"
          target="_blank"
          rel="noreferrer"
          className="text-purple-600 dark:text-purple-400 hover:text-purple-700 dark:hover:text-purple-300 underline"
        >
          Find them in your app at api.slack.com/apps
        </a>
      </p>
      <form onSubmit={handleConnectToken} className="space-y-2">
        <div>
          <label htmlFor="slack-access-token" className="block text-xs text-slate-600 dark:text-slate-400 mb-1">Access Token</label>
          <input
            id="slack-access-token"
            type="password"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="xoxb-your-bot-access-token"
            className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-purple-500/50"
            data-testid="slack-access-token-input"
          />
        </div>
        <div>
          <label htmlFor="slack-app-id" className="block text-xs text-slate-600 dark:text-slate-400 mb-1">App ID</label>
          <input
            id="slack-app-id"
            type="text"
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="A0XXXXXXXXX"
            className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-purple-500/50"
            data-testid="slack-app-id-input"
          />
        </div>
        {connectError && <p className="text-xs text-red-600 dark:text-red-400">{connectError}</p>}
        <button
          type="submit"
          disabled={connecting || !accessToken.trim()}
          data-testid="slack-connect-btn"
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
        >
          <Icon name="forum" size={16} />
          {connecting ? 'Connecting...' : 'Connect to Slack'}
        </button>
      </form>
    </div>
  )
}