import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
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

  return (
    <div data-testid="slack-connect-disconnected">
      <button
        onClick={handleConnect}
        disabled={!configured}
        data-testid="slack-connect-btn"
        className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Icon name="forum" size={16} />
        Connect your Slack workspace
      </button>
      {configured ? (
        <p className="text-xs text-slate-500 mt-1.5">One-click sign in via Slack OAuth</p>
      ) : (
        <p className="text-xs text-slate-500 mt-1.5">
          Set up Slack credentials first. Go to the{' '}
          <Link to="/slack" className="text-purple-400 hover:text-purple-300">Slack page</Link>{' '}
          to add your Client ID and Secret.
        </p>
      )}
    </div>
  )
}
