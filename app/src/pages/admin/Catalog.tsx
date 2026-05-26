import { useState, useEffect, useCallback } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface CatalogEntry {
  id: string
  kind: 'agentfile'
  name: string
  version: number
  content: string
  signed_by_org: string
  published_at: string
  status: 'published' | 'rolled_back'
}

export default function AdminCatalog() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [entries, setEntries] = useState<CatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Publish form
  const [publishName, setPublishName] = useState('')
  const [publishContent, setPublishContent] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState('')
  const [publishSuccess, setPublishSuccess] = useState('')

  // Version history drawer
  const [historyName, setHistoryName] = useState<string | null>(null)
  const [historyVersions, setHistoryVersions] = useState<CatalogEntry[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // Install feedback
  const [installingId, setInstallingId] = useState<string | null>(null)
  const [installFeedback, setInstallFeedback] = useState<Record<string, string>>({})

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'
  const inputCls = `w-full rounded-lg border px-3 py-2 text-sm ${darkMode ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'}`
  const btnPrimary = 'rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50'
  const btnSecondary = `rounded-lg border px-3 py-1.5 text-xs font-medium ${darkMode ? 'border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800' : 'border-slate-300 text-slate-700 hover:bg-slate-50'}`

  const fetchEntries = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.get<{ entries: CatalogEntry[] }>('/team/catalog')
      setEntries(res.entries || [])
    } catch {
      setError('Could not load the catalog. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchEntries() }, [fetchEntries])

  const handlePublish = async () => {
    if (!publishName.trim() || !publishContent.trim()) return
    setPublishing(true)
    setPublishError('')
    setPublishSuccess('')
    try {
      await api.post('/team/catalog/agentfiles', {
        name: publishName.trim(),
        content: publishContent,
      })
      setPublishName('')
      setPublishContent('')
      setPublishSuccess(`"${publishName.trim()}" published.`)
      fetchEntries()
    } catch (err: unknown) {
      setPublishError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setPublishing(false)
    }
  }

  const handleRollback = async (entry: CatalogEntry) => {
    try {
      await api.post(`/team/catalog/${entry.id}/rollback`, {})
      fetchEntries()
      if (historyName === entry.name) fetchHistory(entry.name)
    } catch {
      // ignore, UI will refresh
    }
  }

  const handleInstall = async (entry: CatalogEntry) => {
    setInstallingId(entry.id)
    try {
      const res = await api.post<{ installed_path: string }>(`/team/catalog/${entry.id}/install`, {})
      setInstallFeedback((p) => ({ ...p, [entry.id]: `Installed to ${res.installed_path}` }))
    } catch {
      setInstallFeedback((p) => ({ ...p, [entry.id]: 'Install failed.' }))
    } finally {
      setInstallingId(null)
    }
  }

  const fetchHistory = async (name: string) => {
    setHistoryName(name)
    setHistoryLoading(true)
    try {
      const res = await api.get<{ versions: CatalogEntry[] }>(`/team/catalog/${encodeURIComponent(name)}/versions`)
      setHistoryVersions(res.versions || [])
    } catch {
      setHistoryVersions([])
    } finally {
      setHistoryLoading(false)
    }
  }

  return (
    <div data-testid="catalog-page" className="space-y-6 p-6">
      <div className="flex items-center gap-2">
        <Icon name="inventory_2" className={`text-2xl ${headingCls}`} />
        <h1 className={`text-xl font-semibold ${headingCls}`}>Team Catalog</h1>
      </div>
      <p className={`text-sm ${labelCls}`}>
        Shared agentfiles your team can install. Admins publish new versions; members install with one click.
      </p>

      {/* Publish form */}
      <div className={cardCls}>
        <h2 className={`mb-4 text-sm font-semibold ${headingCls}`}>Publish an agentfile</h2>
        <div className="space-y-3">
          <input
            data-testid="publish-name-input"
            className={inputCls}
            placeholder="Name (e.g. code-reviewer)"
            value={publishName}
            onChange={(e) => setPublishName(e.target.value)}
          />
          <textarea
            data-testid="publish-content-input"
            className={`${inputCls} h-32 resize-none font-mono text-xs`}
            placeholder="Agentfile content…"
            value={publishContent}
            onChange={(e) => setPublishContent(e.target.value)}
          />
          {publishError && (
            <p data-testid="publish-error" className="text-xs text-red-500">{publishError}</p>
          )}
          {publishSuccess && (
            <p data-testid="publish-success" className="text-xs text-green-500">{publishSuccess}</p>
          )}
          <button
            data-testid="publish-button"
            className={btnPrimary}
            disabled={publishing || !publishName.trim() || !publishContent.trim()}
            onClick={handlePublish}
          >
            {publishing ? 'Publishing…' : 'Publish'}
          </button>
        </div>
      </div>

      {/* Catalog list */}
      <div className={cardCls}>
        <h2 className={`mb-4 text-sm font-semibold ${headingCls}`}>Published resources</h2>
        {loading && (
          <p data-testid="catalog-loading" className={`text-sm ${labelCls}`}>Loading…</p>
        )}
        {error && (
          <p data-testid="catalog-error" className="text-sm text-red-500">{error}</p>
        )}
        {!loading && !error && entries.length === 0 && (
          <p data-testid="catalog-empty" className={`text-sm ${labelCls}`}>
            No resources published yet. Use the form above to add one.
          </p>
        )}
        {!loading && entries.length > 0 && (
          <ul data-testid="catalog-list" className="divide-y divide-slate-200 dark:divide-slate-800/20 space-y-0">
            {entries.map((entry) => (
              <li key={entry.id} data-testid={`catalog-entry-${entry.id}`} className="py-4 first:pt-0 last:pb-0">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Icon name="description" className={`text-base ${labelCls}`} />
                      <span className={`font-medium text-sm ${valueCls}`}>{entry.name}</span>
                      <span className={`text-xs ${labelCls}`}>v{entry.version}</span>
                    </div>
                    <p className={`mt-1 text-xs ${labelCls}`}>
                      Published {new Date(entry.published_at).toLocaleDateString()} by {entry.signed_by_org}
                    </p>
                    {installFeedback[entry.id] && (
                      <p data-testid={`install-feedback-${entry.id}`} className="mt-1 text-xs text-green-500">
                        {installFeedback[entry.id]}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      data-testid={`history-btn-${entry.id}`}
                      className={btnSecondary}
                      onClick={() => fetchHistory(entry.name)}
                    >
                      History
                    </button>
                    <button
                      data-testid={`install-btn-${entry.id}`}
                      className={btnSecondary}
                      disabled={installingId === entry.id}
                      onClick={() => handleInstall(entry)}
                    >
                      {installingId === entry.id ? 'Installing…' : 'Install'}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Version history drawer */}
      {historyName && (
        <div data-testid="version-history" className={cardCls}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className={`text-sm font-semibold ${headingCls}`}>
              Version history — {historyName}
            </h2>
            <button
              data-testid="close-history"
              className={btnSecondary}
              onClick={() => setHistoryName(null)}
            >
              Close
            </button>
          </div>
          {historyLoading && <p className={`text-sm ${labelCls}`}>Loading…</p>}
          {!historyLoading && historyVersions.length > 0 && (
            <ul className="space-y-2">
              {historyVersions.map((v) => (
                <li key={v.id} data-testid={`version-row-${v.id}`} className="flex items-center justify-between gap-4">
                  <div>
                    <span className={`text-sm font-medium ${valueCls}`}>v{v.version}</span>
                    <span className={`ml-2 text-xs ${labelCls}`}>
                      {v.status === 'published' ? '(current)' : v.status}
                    </span>
                    <span className={`ml-2 text-xs ${labelCls}`}>
                      {new Date(v.published_at).toLocaleDateString()}
                    </span>
                  </div>
                  {v.status !== 'published' && (
                    <button
                      data-testid={`rollback-btn-${v.id}`}
                      className={btnSecondary}
                      onClick={() => handleRollback(v)}
                    >
                      Restore this version
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
