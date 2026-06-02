import { useState, useEffect } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

interface GithubStatus {
  connected: boolean
  repo?: string
}

export default function GithubConnect() {
  const [status, setStatus] = useState<GithubStatus | null>(null)
  const [oauthAvailable, setOauthAvailable] = useState(false)
  const [forceTokenForm, setForceTokenForm] = useState(false)
  const [ownerRepo, setOwnerRepo] = useState('')
  const [token, setToken] = useState('')
  const [connectStatus, setConnectStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [disconnecting, setDisconnecting] = useState(false)

  const fetchStatus = () => {
    api.get<GithubStatus>('/github/status')
      .then(setStatus)
      .catch(() => setStatus({ connected: false }))
  }

  useEffect(() => {
    fetchStatus()
    api.get<{ oauth_available?: boolean }>('/github/defaults')
      .then((data) => setOauthAvailable(Boolean(data.oauth_available)))
      .catch(() => {})
  }, [])

  const handleConnect = () => {
    setConnectStatus('connecting')
    setError(null)
    api.post('/github/connect', { repo: ownerRepo, token })
      .then(() => { setConnectStatus('done'); fetchStatus() })
      .catch((e: Error) => {
        setConnectStatus('error')
        setError(e?.message ?? 'Connection failed')
      })
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.delete('/github/disconnect')
      setStatus({ connected: false })
      setConnectStatus('idle')
    } catch {
      // ignore
    } finally {
      setDisconnecting(false)
    }
  }

  if (!status) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <div className="w-2.5 h-2.5 rounded-full bg-slate-600 animate-pulse" />
        Checking GitHub...
      </div>
    )
  }

  if (status.connected) {
    if (status.repo === 'owner/repo') {
      return (
        <div data-testid="github-connect-disconnected">
          <p className="text-xs text-amber-600 dark:text-amber-400 mb-3">
            GitHub setup is incomplete. No repo was saved. Click below to reconnect with your real repository.
          </p>
          <button
            onClick={handleDisconnect}
            disabled={disconnecting}
            data-testid="github-reconnect-btn"
            className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-800 border border-amber-400 hover:border-amber-500 disabled:opacity-50 rounded-lg text-sm font-medium text-slate-900 dark:text-white transition-colors"
          >
            <Icon name="settings" size={16} />
            {disconnecting ? 'Clearing…' : 'Set up GitHub connection'}
          </button>
        </div>
      )
    }

    return (
      <div className="flex items-center justify-between" data-testid="github-connect-connected">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
          <p className="text-sm text-slate-800 dark:text-slate-200 font-medium">
            {status.repo || 'Connected'}
          </p>
        </div>
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          data-testid="github-disconnect-btn"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-sm text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors disabled:opacity-50 flex-shrink-0"
        >
          <Icon name="link_off" size={15} />
          Disconnect
        </button>
      </div>
    )
  }

  if (oauthAvailable && !forceTokenForm) {
    return (
      <div data-testid="github-connect-disconnected">
        <p className="text-xs text-slate-500 mb-3">
          One click — GitHub will ask for permission, then you pick which repo to track.
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={async () => {
              try {
                const res = await api.get<{ url: string }>('/github/auth-url')
                window.location.href = res.url
              } catch (e: any) {
                // Fall back to the manual token form and surface the reason.
                setError(e?.message ?? 'Could not start the GitHub connection.')
                setForceTokenForm(true)
              }
            }}
            className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:border-slate-500 rounded-lg text-sm font-medium text-slate-900 dark:text-white transition-colors"
            data-testid="github-oauth-btn"
          >
            <Icon name="code" size={16} />
            Connect GitHub
          </button>
          <button
            onClick={() => setForceTokenForm(true)}
            className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 underline"
            data-testid="github-use-token-btn"
          >
            Use a token instead
          </button>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="github-connect-disconnected">
      <div className="space-y-2">
        <p className="text-xs text-slate-500">
          Manual setup: connect with a personal access token and repository.
        </p>
        <p className="text-xs text-slate-500">
          <a
            href="https://github.com/settings/tokens/new?scopes=repo,read:user&description=yourOS"
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
          >
            Create a GitHub token
          </a>
        </p>
        <input
          type="text"
          value={ownerRepo}
          onChange={(e) => setOwnerRepo(e.target.value)}
          placeholder="owner/repo (e.g. acme/website)"
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="github-repo-input"
        />
        {ownerRepo.trim().toLowerCase() === 'owner/repo' && (
          <p className="text-xs text-amber-600 dark:text-amber-400">Enter your actual repo, e.g. acme/website</p>
        )}
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Personal access token"
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="github-token-input"
        />
        {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex items-center gap-3">
          <button
            onClick={handleConnect}
            disabled={connectStatus === 'connecting' || connectStatus === 'done' || !ownerRepo.trim() || ownerRepo.trim().toLowerCase() === 'owner/repo'}
            className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:border-slate-500 disabled:opacity-50 rounded-lg text-sm font-medium text-slate-900 dark:text-white transition-colors"
            data-testid="github-connect-btn"
          >
            <Icon name="code" size={16} />
            {connectStatus === 'done' ? 'Connected!' : connectStatus === 'connecting' ? 'Connecting...' : 'Connect GitHub'}
          </button>
          {oauthAvailable && (
            <button
              onClick={() => setForceTokenForm(false)}
              className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 underline"
            >
              Use OAuth instead
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
