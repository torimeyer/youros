import { useState, useEffect } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

interface AtlassianStatus {
  connected: boolean
  email?: string
  site?: string
  jira_site?: string
  confluence_site?: string
  confluence_available?: boolean
  expired?: boolean
}

interface AtlassianConnectProps {
  product?: 'jira' | 'confluence'
}

export default function AtlassianConnect({ product }: AtlassianConnectProps = {}) {
  const connectLabel = product === 'jira' ? 'Connect Jira' : product === 'confluence' ? 'Connect Confluence' : 'Connect Atlassian'
  const [status, setStatus] = useState<AtlassianStatus | null>(null)
  const [oauthAvailable, setOauthAvailable] = useState(false)
  const [forceTokenForm, setForceTokenForm] = useState(false)
  const [site, setSite] = useState('')
  const [confluenceSite, setConfluenceSite] = useState('')
  const [separateConfluence, setSeparateConfluence] = useState(true)
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [connectStatus, setConnectStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [disconnecting, setDisconnecting] = useState(false)

  const fetchStatus = () => {
    api.get<AtlassianStatus>('/atlassian/status')
      .then(setStatus)
      .catch(() => setStatus({ connected: false }))
  }

  useEffect(() => {
    fetchStatus()
    api.get<{ site?: string; email?: string; oauth_available?: boolean }>('/atlassian/defaults')
      .then((data) => {
        if (data.site) setSite(data.site)
        if (data.email) setEmail(data.email)
        if (data.oauth_available) setOauthAvailable(true)
      })
      .catch(() => {})
  }, [])

  const handleConnect = () => {
    setConnectStatus('connecting')
    setError(null)
    const payload = separateConfluence
      ? { jira_site: site, confluence_site: confluenceSite, email, api_token: token }
      : { site, email, api_token: token }
    api.post('/atlassian/connect', payload)
      .then(() => { setConnectStatus('done'); fetchStatus() })
      .catch((e: Error) => {
        setConnectStatus('error')
        setError(e?.message ?? 'Connection failed')
      })
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.delete('/atlassian/disconnect')
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
        Checking Atlassian...
      </div>
    )
  }

  if (status.connected && status.expired) {
    return (
      <div className="flex items-center justify-between" data-testid="atlassian-connect-expired">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-amber-400 flex-shrink-0" />
          <p className="text-sm text-amber-700 dark:text-amber-300">Atlassian credentials expired</p>
        </div>
        <button
          onClick={() => {
            const p = new URLSearchParams({ return_to: '/settings' })
            if (status?.jira_site) p.set('jira_site', status.jira_site)
            if (status?.confluence_site && status.confluence_site !== status.jira_site) p.set('confluence_site', status.confluence_site)
            window.location.href = `/api/atlassian/auth?${p.toString()}`
          }}
          data-testid="atlassian-reconnect-btn"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
        >
          <Icon name="refresh" size={15} />
          Reconnect
        </button>
      </div>
    )
  }

  if (status.connected) {
    return (
      <div className="flex items-center justify-between" data-testid="atlassian-connect-connected">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-sm text-slate-800 dark:text-slate-200 font-medium truncate">
              {status.email || status.site || 'Connected'}
            </p>
            {status.site && status.email && (
              <p className="text-xs text-slate-500 truncate">{status.site}</p>
            )}
            <p className="text-xs text-slate-500" data-testid="atlassian-products">
              {[status.jira_site && 'Jira', status.confluence_site && 'Confluence'].filter(Boolean).join(' & ') || 'Jira & Confluence'}
            </p>
          </div>
        </div>
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          data-testid="atlassian-disconnect-btn"
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
      <div data-testid="atlassian-connect-disconnected">
        <p className="text-xs text-slate-500 mb-3">
          One click — Atlassian will ask for permission, then bring you back here.
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              const p = new URLSearchParams({ return_to: '/settings' })
              if (status?.jira_site) p.set('jira_site', status.jira_site)
              if (status?.confluence_site && status.confluence_site !== status.jira_site) p.set('confluence_site', status.confluence_site)
              window.location.href = `/api/atlassian/auth?${p.toString()}`
            }}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="atlassian-oauth-btn"
          >
            <Icon name="link" size={16} />
            {connectLabel}
          </button>
          <button
            onClick={() => setForceTokenForm(true)}
            className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 underline"
            data-testid="atlassian-use-token-btn"
          >
            Use a token instead
          </button>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="atlassian-connect-disconnected">
      <div className="space-y-2">
        <input
          type="text"
          value={site}
          onChange={(e) => setSite(e.target.value)}
          placeholder={separateConfluence ? "Jira site, e.g. https://company.atlassian.net" : "https://company.atlassian.net"}
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="atlassian-site-input"
        />
        <label className="flex items-center gap-2 text-xs cursor-pointer select-none text-slate-500">
          <input
            type="checkbox"
            checked={separateConfluence}
            onChange={(e) => setSeparateConfluence(e.target.checked)}
            data-testid="atlassian-separate-confluence"
          />
          <span>Confluence is on a different site</span>
        </label>
        {separateConfluence && (
          <input
            type="text"
            value={confluenceSite}
            onChange={(e) => setConfluenceSite(e.target.value)}
            placeholder="Confluence site, e.g. https://wiki.company.com"
            className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
            data-testid="atlassian-confluence-site-input"
          />
        )}
        <input
          type="text"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="atlassian-email-input"
        />
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="API token"
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="atlassian-token-input"
        />
        <p className="text-xs text-slate-500">
          <a
            href="https://id.atlassian.com/manage-profile/security/api-tokens"
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
          >
            Get a token at id.atlassian.com
          </a>
        </p>
        {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex items-center gap-3">
          <button
            onClick={handleConnect}
            disabled={connectStatus === 'connecting' || connectStatus === 'done' || !site.trim()}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="atlassian-connect-btn"
          >
            <Icon name="link" size={16} />
            {connectStatus === 'done' ? 'Connected!' : connectStatus === 'connecting' ? 'Connecting...' : connectLabel}
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
