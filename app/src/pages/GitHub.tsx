import { useState, useEffect, useCallback } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
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
    try {
      const res = await api.get<{ issues: GitHubIssue[] }>('/github/issues')
      const fetched = res.issues || []
      setIssues(fetched)
      writeIssueCache(fetched)
    } catch {
      setIssues((prev) => (prev.length > 0 ? prev : []))
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
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="GitHub" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 flex items-center gap-2 text-slate-400">
          <Icon name="progress_activity" size={20} className="animate-spin" />
          Loading...
        </div>
      </div>
    )
  }

  if (!status?.connected) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="GitHub" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 max-w-md mx-auto">
          <div className="bg-slate-900/40 border border-slate-800 p-5 sm:p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-slate-500/20 flex items-center justify-center mb-4">
              <Icon name="code" className="text-slate-300" size={24} />
            </div>
            <h2 className="text-xl font-semibold mb-2">Connect GitHub</h2>
            <p className="text-slate-400 mb-6">
              Import issues from a GitHub repository and sync them with your myOS tasks.
              You need a personal access token with repo scope.
            </p>
            <div className="space-y-3 mb-4">
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
            </div>
            <button
              onClick={handleConnect}
              disabled={connecting}
              className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors disabled:opacity-50"
            >
              {connecting ? 'Connecting...' : 'Connect'}
            </button>
            <p className="mt-3 text-xs text-slate-500">
              Create a token at{' '}
              <a href="https://github.com/settings/tokens/new" target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">
                github.com/settings/tokens
              </a>
              {' '}with the <code className="text-slate-300">repo</code> scope.
            </p>
            {connectError && (
              <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
                {connectError}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="GitHub" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl sm:text-2xl font-bold">GitHub</h1>
              {status.repo && (
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
        {syncResult && (
          <div className={`mb-4 p-3 rounded-lg text-sm ${syncResult.errors.length > 0 ? 'bg-amber-500/10 border border-amber-500/30 text-amber-300' : 'bg-green-500/10 border border-green-500/30 text-green-300'}`}>
            {syncResult.created > 0 && <span>Created {syncResult.created} tasks. </span>}
            {syncResult.skipped > 0 && <span>Skipped {syncResult.skipped} (already in myOS). </span>}
            {syncResult.errors.map((e, i) => <span key={i} className="block">{e}</span>)}
          </div>
        )}

        {/* Issues list */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="bug_report" className="text-green-400" size={18} />
            <h2 className="text-base font-semibold">Open Issues</h2>
            <span className="text-xs text-slate-500">{issues.length}</span>
          </div>

          {issues.length === 0 ? (
            <div className="text-center py-8 text-slate-500">
              <Icon name="check_circle" size={36} className="mb-2 mx-auto opacity-40" />
              <p>No open issues in this repository.</p>
            </div>
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
