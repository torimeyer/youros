import { useState, useEffect, useCallback } from 'react'
import { formatDate } from '../lib/time'
import { useParams, useNavigate } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { ConnectCard, LoadingState, EmptyState, ErrorBanner } from '../components/ui'
import { api } from '../lib/api'

interface AtlassianStatus {
  connected: boolean
  email: string
  site: string
  jira_url?: string
  confluence_url?: string
}

interface ConfluencePage {
  id: string
  title: string
  space_id: string
  updated: string
  url: string
}

interface ConfluencePageDetail extends ConfluencePage {
  body_html: string
  created: string
}



export default function Confluence() {
  const { pageId } = useParams<{ pageId?: string }>()
  const navigate = useNavigate()

  const [status, setStatus] = useState<AtlassianStatus | null>(null)
  const [pages, setPages] = useState<ConfluencePage[]>([])
  const [detail, setDetail] = useState<ConfluencePageDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [connectEmail, setConnectEmail] = useState('')
  const [connectToken, setConnectToken] = useState('')
  const [connectSite, setConnectSite] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [oauthAvailable, setOauthAvailable] = useState(false)
  const [forceTokenForm, setForceTokenForm] = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<AtlassianStatus>('/atlassian/status')
      setStatus(res)
      return res
    } catch {
      setStatus({ connected: false, email: '', site: '' })
      return null
    }
  }, [])

  const fetchPages = useCallback(async () => {
    try {
      const res = await api.get<{ pages: ConfluencePage[] }>('/atlassian/confluence/pages')
      setPages(res.pages || [])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load pages')
    }
  }, [])

  const fetchDetail = useCallback(async (id: string) => {
    setDetailLoading(true)
    setError(null)
    try {
      const res = await api.get<ConfluencePageDetail>(`/atlassian/confluence/page/${id}`)
      setDetail(res)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load page')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    setForceTokenForm(false)
    ;(async () => {
      try {
        const defaults = await api.get<{ site: string; oauth_available?: boolean }>('/atlassian/defaults')
        if (defaults.site) setConnectSite(defaults.site)
        if (defaults.oauth_available) setOauthAvailable(true)
      } catch {
        // ignore — pre-fill is best-effort
      }
    })()
  }, [])

  useEffect(() => {
    ;(async () => {
      setLoading(true)
      const s = await fetchStatus()
      if (s?.connected) {
        if (pageId) {
          await fetchDetail(pageId)
        } else {
          await fetchPages()
        }
      }
      setLoading(false)
    })()
  }, [pageId, fetchStatus, fetchPages, fetchDetail])

  const handleConnect = async () => {
    setConnectError(null)
    if (!connectEmail.trim() || !connectToken.trim() || !connectSite.trim()) {
      setConnectError('All fields are required.')
      return
    }
    setConnecting(true)
    try {
      await api.post('/atlassian/connect', {
        email: connectEmail,
        api_token: connectToken,
        site: connectSite,
      })
      const s = await fetchStatus()
      if (s?.connected) {
        await fetchPages()
      }
      setConnectEmail('')
      setConnectToken('')
      setConnectSite('')
    } catch (err: unknown) {
      setConnectError(err instanceof Error ? err.message : 'Connection failed')
    } finally {
      setConnecting(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await api.delete('/atlassian/disconnect')
      setStatus({ connected: false, email: '', site: '' })
      setPages([])
      setDetail(null)
    } catch {
      // ignore
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Confluence" />
        <div className="px-4 pb-4 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (!status?.connected) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white" data-testid="confluence-page">
        <TopBar title="Confluence" />
        <div className="px-4 pb-4 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="menu_book"
            accentColor="#94a3b8"
            title="Connect Confluence"
            description="See your recently-updated Confluence pages inside myOS. You need an Atlassian API token."
            primaryAction={
              oauthAvailable && !forceTokenForm ? (
                <div className="w-full space-y-3 text-left">
                  <button
                    onClick={() => window.open('/api/atlassian/auth', '_self')}
                    className="w-full py-3 bg-blue-600 hover:bg-blue-500 rounded-xl font-medium transition-colors text-white"
                    data-testid="confluence-oauth-connect"
                  >
                    Connect Confluence
                  </button>
                  <button
                    onClick={() => setForceTokenForm(true)}
                    className="text-xs text-slate-400 underline hover:opacity-80"
                    data-testid="confluence-use-token"
                  >
                    Use a token instead
                  </button>
                </div>
              ) : (
                <div className="w-full space-y-3 text-left">
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Site URL</label>
                    <input
                      type="text"
                      value={connectSite}
                      onChange={(e) => setConnectSite(e.target.value)}
                      placeholder="yourco.atlassian.net"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Email</label>
                    <input
                      type="email"
                      value={connectEmail}
                      onChange={(e) => setConnectEmail(e.target.value)}
                      placeholder="you@company.com"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">API Token</label>
                    <input
                      type="password"
                      value={connectToken}
                      onChange={(e) => setConnectToken(e.target.value)}
                      placeholder="ATATT3x..."
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                    />
                  </div>
                  <button
                    onClick={handleConnect}
                    disabled={connecting}
                    className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors disabled:opacity-50"
                  >
                    {connecting ? 'Connecting...' : 'Connect'}
                  </button>
                  <p className="text-xs text-slate-500">
                    Create a token at{' '}
                    <a
                      href="https://id.atlassian.com/manage-profile/security/api-tokens"
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-400 hover:text-blue-300"
                    >
                      id.atlassian.com
                    </a>
                  </p>
                </div>
              )
            }
            error={connectError ?? undefined}
          />
        </div>
      </div>
    )
  }

  // Detail view
  if (pageId) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white" data-testid="confluence-page">
        <TopBar title="Confluence" />
        <div className="px-4 pb-4 sm:px-8 sm:pb-8">
          <button
            onClick={() => navigate('/confluence')}
            className="flex items-center gap-1.5 text-slate-400 hover:text-white text-sm mb-4 transition-colors"
          >
            <Icon name="arrow_back" size={16} />
            Back to pages
          </button>

          {error && (
            <div className="mb-4">
              <ErrorBanner message={error} />
            </div>
          )}

          {detailLoading && <LoadingState variant="spinner" />}

          {detail && !detailLoading && (
            <div className={cardClass}>
              <div className="flex items-start justify-between gap-3 mb-4">
                <h1 className="text-lg font-semibold">{detail.title}</h1>
                <a
                  href={detail.url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                  title="Open in Confluence"
                >
                  <Icon name="open_in_new" size={16} className="text-slate-400" />
                </a>
              </div>

              <div className="flex items-center gap-4 text-xs text-slate-500 mb-6">
                {detail.updated && <span>Updated {formatDate(detail.updated)}</span>}
              </div>

              {detail.body_html ? (
                <div
                  className="prose prose-invert prose-sm max-w-none text-slate-300"
                  dangerouslySetInnerHTML={{ __html: detail.body_html }}
                />
              ) : (
                <EmptyState icon="article" title="No content" />
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  // List view
  return (
    <div className="min-h-dvh bg-slate-950 text-white" data-testid="confluence-page">
      <TopBar title="Confluence" />
      <div className="px-4 pb-4 sm:px-8 sm:pb-8">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl sm:text-2xl font-bold">Confluence</h1>
              {status.site && (
                <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 text-sm font-mono rounded-full">
                  {status.site}
                </span>
              )}
            </div>
            {(status.jira_url || status.confluence_url) && (
              <div className="flex gap-4 mt-1">
                {status.jira_url && (
                  <span className="text-xs text-slate-500" data-testid="confluence-jira-url">{status.jira_url}</span>
                )}
                {status.confluence_url && (
                  <span className="text-xs text-slate-500" data-testid="confluence-confluence-url">{status.confluence_url}</span>
                )}
              </div>
            )}
          </div>
          <button
            onClick={handleDisconnect}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors text-slate-400"
          >
            <Icon name="link_off" size={16} />
            Disconnect
          </button>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorBanner message={error} action={{ label: 'Retry', onClick: fetchPages }} />
          </div>
        )}

        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="menu_book" className="text-blue-400" size={18} />
            <h2 className="text-base font-semibold">Recent Pages</h2>
            <span className="text-xs text-slate-500">{pages.length}</span>
          </div>

          {pages.length === 0 ? (
            <EmptyState icon="article" title="No recent pages" />
          ) : (
            <div className="divide-y divide-slate-800/60">
              {pages.map((page) => (
                <button
                  key={page.id}
                  data-testid={`confluence-row-${page.id}`}
                  onClick={() => navigate(`/confluence/${page.id}`)}
                  className="w-full text-left py-3 px-2 hover:bg-slate-800/30 rounded-lg transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Icon name="article" size={14} className="text-slate-500 shrink-0" />
                    <span className="flex-1 text-sm font-medium text-white truncate">
                      {page.title}
                    </span>
                    <span className="text-xs text-slate-500 shrink-0">{formatDate(page.updated)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
