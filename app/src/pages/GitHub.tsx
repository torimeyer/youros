import { useState, useEffect, useCallback } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { ConnectCard, LoadingState, EmptyState, ErrorBanner } from '../components/ui'
import { api } from '../lib/api'

interface GitHubIssue {
  number: number
  title: string
  state: string
  body: string
  labels: string[]
  assignee: string
  created_at: string
  updated_at: string
  html_url: string
}

interface GitHubStatus {
  connected: boolean
  repo: string
}

interface SyncResult {
  ok: boolean
  created: number
  skipped: number
  total_issues: number
  errors: string[]
}

// Seed from localStorage
const GITHUB_ISSUES_CACHE_KEY = 'myos.githubIssues.v1'

function readIssueCache(): GitHubIssue[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(GITHUB_ISSUES_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as GitHubIssue[]) : []
  } catch {
    return []
  }
}

function writeIssueCache(issues: GitHubIssue[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(GITHUB_ISSUES_CACHE_KEY, JSON.stringify(issues))
  } catch {
    // not fatal
  }
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

export default function GitHub() {
  const [status, setStatus] = useState<GitHubStatus | null>(null)
  const [issues, setIssues] = useState<GitHubIssue[]>(() => readIssueCache())
  const [loading, setLoading] = useState<boolean>(() => readIssueCache().length === 0)
  const [refreshing, setRefreshing] = useState<boolean>(false)
  const [connectToken, setConnectToken] = useState('')
  const [connectRepo, setConnectRepo] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null)
  const [pushing, setPushing] = useState<Set<number>>(new Set())

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<GitHubStatus>('/github/status')
      setStatus(res)
    } catch {
      setStatus({ connected: false, repo: '' })
    }
  }, [])

  const fetchIssues = useCallback(async () => {
    setRefreshing(true)
    try {
      const res = await api.get<{ issues: GitHubIssue[] }>('/github/issues')
      const fetched = res.issues || []
      setIssues(fetched)
      writeIssueCache(fetched)
    } catch {
      setIssues((prev) => (prev.length > 0 ? prev : []))
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const hasCached = readIssueCache().length > 0
    if (!hasCached) setLoading(true)
    ;(async () => {
      try {
        const s = await api.get<GitHubStatus>('/github/status')
        setStatus(s)
        if (s.connected) {
          await fetchIssues()
        }
      } catch {
        setStatus({ connected: false, repo: '' })
      }
      setLoading(false)
    })()
  }, [fetchIssues])

  const handleConnect = async () => {
    setConnectError(null)
    if (!connectToken.trim() || !connectRepo.trim()) {
      setConnectError('Both token and repository are required.')
      return
    }
    setConnecting(true)
    try {
      await api.post('/github/connect', { token: connectToken, repo: connectRepo })
      await fetchStatus()
      await fetchIssues()
      setConnectToken('')
      setConnectRepo('')
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Connection failed'
      setConnectError(message)
    } finally {
      setConnecting(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await api.delete('/github/disconnect')
      setStatus({ connected: false, repo: '' })
      setIssues([])
    } catch {
      // ignore
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const res = await api.post<SyncResult>('/github/sync', {})
      setSyncResult(res)
      await fetchIssues()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Sync failed'
      setSyncResult({ ok: false, created: 0, skipped: 0, total_issues: 0, errors: [message] })
    } finally {
      setSyncing(false)
    }
  }

  const handlePushToGithub = async (issue: GitHubIssue) => {
    setPushing((prev) => new Set(prev).add(issue.number))
    try {
      await api.post(`/github/push/${issue.number}`, {
        title: issue.title,
        body: issue.body,
        labels: issue.labels,
      })
    } catch {
      // ignore
    } finally {
      setPushing((prev) => {
        const next = new Set(prev)
        next.delete(issue.number)
        return next
      })
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="GitHub" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  // If status is still loading but we have seeded issues from the last
  // session, paint them now rather than flashing the Connect card. The
  // server response will replace the rows shortly.
  const hasSeededIssues = issues.length > 0
  const showConnectCard = status ? !status.connected : !hasSeededIssues

  // Post-OAuth pick-repo: when the OAuth callback comes back with
  // ?oauth_connected=true, status.connected is already true (the
  // callback saved the token) but status.repo is empty. Show a
  // narrower card that asks ONLY for a repo name. The connect handler
  // posts {repo} only — the backend reuses the saved OAuth token.
  const oauthPickRepo = !!(status?.connected && !status.repo)
  if (oauthPickRepo) {
    const handlePickRepo = async () => {
      setConnectError(null)
      if (!connectRepo.trim()) {
        setConnectError('Repository is required.')
        return
      }
      setConnecting(true)
      try {
        await api.post('/github/connect', { repo: connectRepo.trim() })
        // Strip ?oauth_connected so a refresh does not hit this path again.
        const params = new URLSearchParams(window.location.search)
        params.delete('oauth_connected')
        const next = params.toString()
        window.history.replaceState(
          {},
          '',
          window.location.pathname + (next ? `?${next}` : ''),
        )
        await fetchStatus()
        await fetchIssues()
        setConnectRepo('')
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Connection failed'
        setConnectError(message)
      } finally {
        setConnecting(false)
      }
    }
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="GitHub" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="code"
            accentColor="#94a3b8"
            title="GitHub connected — pick a repo"
            description="Tell us which repository to track. You can change it later."
            primaryAction={
              <div className="w-full space-y-3 text-left">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Repository</label>
                  <input
                    type="text"
                    value={connectRepo}
                    onChange={(e) => setConnectRepo(e.target.value)}
                    placeholder="owner/repo or https://github.com/owner/repo"
                    data-testid="github-oauth-pick-repo"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                  />
                </div>
                {connectError && (
                  <p className="text-xs text-red-400">{connectError}</p>
                )}
                <button
                  onClick={handlePickRepo}
                  disabled={connecting}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
                  data-testid="github-oauth-pick-repo-submit"
                >
                  {connecting ? 'Saving...' : 'Track this repo'}
                </button>
              </div>
            }
          />
        </div>
      </div>
    )
  }

  if (showConnectCard) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="GitHub" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="code"
            accentColor="#94a3b8"
            title="Connect GitHub"
            description="Import issues from a GitHub repository and sync them with your myOS tasks. You need a personal access token with repo scope."
            primaryAction={
              <div className="w-full space-y-3 text-left">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Personal access token</label>
                  <input
                    type="password"
                    value={connectToken}
                    onChange={(e) => setConnectToken(e.target.value)}
                    placeholder="ghp_..."
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Repository</label>
                  <input
                    type="text"
                    value={connectRepo}
                    onChange={(e) => setConnectRepo(e.target.value)}
                    placeholder="owner/repo or https://github.com/owner/repo"
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
                  <a href="https://github.com/settings/tokens/new" target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">
                    github.com/settings/tokens
                  </a>
                  {' '}with the <code className="text-slate-300">repo</code> scope.
                </p>
              </div>
            }
            error={connectError ?? undefined}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="GitHub" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl sm:text-2xl font-bold">GitHub</h1>
              {status?.repo && (
                <a
                  href={`https://github.com/${status.repo}`}
                  target="_blank"
                  rel="noreferrer"
                  className="px-2 py-0.5 bg-slate-500/20 text-slate-300 text-sm font-mono rounded-full hover:text-white"
                >
                  {status.repo}
                </a>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleSync}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
            >
              <Icon name="sync" size={16} className={syncing ? 'animate-spin' : ''} />
              Import to myOS
            </button>
            <button
              onClick={handleDisconnect}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors text-slate-400"
            >
              <Icon name="link_off" size={16} />
              Disconnect
            </button>
          </div>
        </div>

        {/* Sync result */}
        {syncResult && syncResult.errors.length > 0 && (
          <div className="mb-4">
            <ErrorBanner
              message={syncResult.errors.join(' ')}
              action={{ label: 'Try again', onClick: handleSync }}
            />
          </div>
        )}
        {syncResult && syncResult.errors.length === 0 && (
          <div className="mb-4 p-3 rounded-xl text-sm bg-green-500/10 border border-green-500/30 text-green-300">
            {syncResult.created > 0 && <span>Created {syncResult.created} tasks. </span>}
            {syncResult.skipped > 0 && <span>Skipped {syncResult.skipped} (already in myOS). </span>}
          </div>
        )}

        {/* Issues list */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="bug_report" className="text-green-400" size={18} />
            <h2 className="text-base font-semibold">Open Issues</h2>
            <span className="text-xs text-slate-500">{issues.length}</span>
            {refreshing && issues.length > 0 && (
              <span className="text-xs text-slate-400 ml-auto" data-testid="github-refreshing">
                Refreshing...
              </span>
            )}
          </div>

          {issues.length === 0 ? (
            <EmptyState icon="check_circle" title="No open issues" />
          ) : (
            <div className="divide-y divide-slate-800/60">
              {issues.map((issue) => (
                <div key={issue.number} className="py-3 px-2 hover:bg-slate-800/30 rounded-lg transition-colors">
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-xs text-slate-500 font-mono">#{issue.number}</span>
                        <a
                          href={issue.html_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm font-medium text-white hover:text-blue-300 truncate"
                        >
                          {issue.title}
                        </a>
                      </div>
                      {issue.labels.length > 0 && (
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {issue.labels.map((label) => (
                            <span key={label} className="px-1.5 py-0.5 bg-slate-700 text-slate-300 text-[10px] rounded-full">
                              {label}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                        {issue.assignee && <span>@{issue.assignee}</span>}
                        <span>Updated {formatDate(issue.updated_at)}</span>
                      </div>
                    </div>
                    <button
                      onClick={() => handlePushToGithub(issue)}
                      disabled={pushing.has(issue.number)}
                      title="Push to GitHub as issue"
                      className="shrink-0 p-1.5 rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50"
                    >
                      <Icon name="open_in_new" size={16} className="text-slate-400" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
