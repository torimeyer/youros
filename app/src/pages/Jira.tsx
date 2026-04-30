import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { ConnectCard, LoadingState, EmptyState, ErrorBanner } from '../components/ui'
import { api } from '../lib/api'

interface AtlassianStatus {
  connected: boolean
  email: string
  site: string
}

interface JiraIssue {
  key: string
  summary: string
  status: string
  priority: string
  type: string
  updated: string
  url: string
}

interface JiraIssueDetail extends JiraIssue {
  description_html: string
  assignee: string
  reporter: string
  created: string
  comments: { author: string; body_html: string; created: string }[]
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleDateString([], { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

function StatusPill({ status }: { status: string }) {
  const color =
    status === 'Done' || status === 'Closed' ? 'bg-green-500/20 text-green-400'
    : status === 'In Progress' ? 'bg-blue-500/20 text-blue-400'
    : 'bg-slate-600/40 text-slate-400'
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${color}`}>
      {status}
    </span>
  )
}

function TypePill({ type }: { type: string }) {
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-700/60 text-slate-400">
      {type}
    </span>
  )
}

export default function Jira() {
  const { key } = useParams<{ key?: string }>()
  const navigate = useNavigate()

  const [status, setStatus] = useState<AtlassianStatus | null>(null)
  const [issues, setIssues] = useState<JiraIssue[]>([])
  const [detail, setDetail] = useState<JiraIssueDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [connectEmail, setConnectEmail] = useState('')
  const [connectToken, setConnectToken] = useState('')
  const [connectSite, setConnectSite] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

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

  const fetchIssues = useCallback(async () => {
    try {
      const res = await api.get<{ issues: JiraIssue[] }>('/atlassian/jira/issues')
      setIssues(res.issues || [])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load issues')
    }
  }, [])

  const fetchDetail = useCallback(async (issueKey: string) => {
    setDetailLoading(true)
    setError(null)
    try {
      const res = await api.get<JiraIssueDetail>(`/atlassian/jira/issue/${issueKey}`)
      setDetail(res)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load issue')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    ;(async () => {
      setLoading(true)
      const s = await fetchStatus()
      if (s?.connected) {
        if (key) {
          await fetchDetail(key)
        } else {
          await fetchIssues()
        }
      }
      setLoading(false)
    })()
  }, [key, fetchStatus, fetchIssues, fetchDetail])

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
        await fetchIssues()
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
      setIssues([])
      setDetail(null)
    } catch {
      // ignore
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Jira" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (!status?.connected) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white" data-testid="jira-page">
        <TopBar title="Jira" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
          <ConnectCard
            icon="bug_report"
            accentColor="#94a3b8"
            title="Connect Atlassian"
            description="See your assigned Jira issues inside myOS. You need an Atlassian API token."
            primaryAction={
              <div className="w-full space-y-3 text-left" data-testid="jira-connect-form">
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
            }
            error={connectError ?? undefined}
          />
        </div>
      </div>
    )
  }

  // Detail view
  if (key) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white" data-testid="jira-page">
        <TopBar title="Jira" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
          <button
            onClick={() => navigate('/jira')}
            className="flex items-center gap-1.5 text-slate-400 hover:text-white text-sm mb-4 transition-colors"
          >
            <Icon name="arrow_back" size={16} />
            Back to issues
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
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono text-slate-500">{detail.key}</span>
                    <StatusPill status={detail.status} />
                    {detail.type && <TypePill type={detail.type} />}
                  </div>
                  <h1 className="text-lg font-semibold">{detail.summary}</h1>
                </div>
                <a
                  href={detail.url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                  title="Open in Jira"
                >
                  <Icon name="open_in_new" size={16} className="text-slate-400" />
                </a>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs text-slate-400 mb-6">
                {detail.assignee && (
                  <div>
                    <div className="text-slate-600 mb-0.5">Assignee</div>
                    <div className="text-slate-300">{detail.assignee}</div>
                  </div>
                )}
                {detail.reporter && (
                  <div>
                    <div className="text-slate-600 mb-0.5">Reporter</div>
                    <div className="text-slate-300">{detail.reporter}</div>
                  </div>
                )}
                {detail.priority && (
                  <div>
                    <div className="text-slate-600 mb-0.5">Priority</div>
                    <div className="text-slate-300">{detail.priority}</div>
                  </div>
                )}
                {detail.updated && (
                  <div>
                    <div className="text-slate-600 mb-0.5">Updated</div>
                    <div className="text-slate-300">{formatDate(detail.updated)}</div>
                  </div>
                )}
              </div>

              {detail.description_html && (
                <div className="mb-6">
                  <h2 className="text-sm font-medium text-slate-400 mb-2">Description</h2>
                  <div
                    className="prose prose-invert prose-sm max-w-none text-slate-300"
                    dangerouslySetInnerHTML={{ __html: detail.description_html }}
                  />
                </div>
              )}

              {detail.comments.length > 0 && (
                <div>
                  <h2 className="text-sm font-medium text-slate-400 mb-3">
                    Comments ({detail.comments.length})
                  </h2>
                  <div className="space-y-4">
                    {detail.comments.map((comment, i) => (
                      <div key={i} className="border-t border-slate-800 pt-3">
                        <div className="flex items-center gap-2 mb-1.5 text-xs text-slate-500">
                          <span className="text-slate-400 font-medium">{comment.author}</span>
                          <span>{formatDate(comment.created)}</span>
                        </div>
                        <div
                          className="prose prose-invert prose-sm max-w-none text-slate-300"
                          dangerouslySetInnerHTML={{ __html: comment.body_html }}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  // List view
  return (
    <div className="min-h-dvh bg-slate-950 text-white" data-testid="jira-page">
      <TopBar title="Jira" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold">Jira</h1>
            {status.site && (
              <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 text-sm font-mono rounded-full">
                {status.site}
              </span>
            )}
          </div>
          <button
            onClick={handleDisconnect}
            data-testid="jira-disconnect"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors text-slate-400"
          >
            <Icon name="link_off" size={16} />
            Disconnect
          </button>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorBanner message={error} action={{ label: 'Retry', onClick: fetchIssues }} />
          </div>
        )}

        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="bug_report" className="text-blue-400" size={18} />
            <h2 className="text-base font-semibold">My Issues</h2>
            <span className="text-xs text-slate-500">{issues.length}</span>
          </div>

          {issues.length === 0 ? (
            <EmptyState icon="check_circle" title="No issues assigned to you" />
          ) : (
            <div className="divide-y divide-slate-800/60">
              {issues.map((issue) => (
                <button
                  key={issue.key}
                  data-testid={`jira-issue-row-${issue.key}`}
                  onClick={() => navigate(`/jira/${issue.key}`)}
                  className="w-full text-left py-3 px-2 hover:bg-slate-800/30 rounded-lg transition-colors"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono text-slate-500 shrink-0">{issue.key}</span>
                    <StatusPill status={issue.status} />
                    {issue.type && <TypePill type={issue.type} />}
                    <span className="flex-1 text-sm font-medium text-white truncate">
                      {issue.summary}
                    </span>
                    <span className="text-xs text-slate-500 shrink-0">{formatDate(issue.updated)}</span>
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
