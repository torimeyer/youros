import { useState, useEffect } from 'react'
import TopBar from '../components/TopBar'
import { api } from '../lib/api'
import Icon from '../components/Icon'

interface InboxItem {
  id: string
  kind: string
  title: string
  preview: string
  source: string
  source_ref: string
  permalink: string | null
  created_at: string
  raw: Record<string, unknown>
}

const CACHE_KEY = 'myos.inboxCache.v1'

function sourceLabel(source: string): string {
  if (source === 'slack') return 'Slack'
  if (source === 'meeting') return 'Meeting'
  if (source === 'email') return 'Email'
  return source || 'Unknown'
}

function kindLabel(kind: string): string {
  if (kind === 'slack') return 'Flagged messages'
  if (kind === 'task') return 'Source-tagged tasks'
  return kind
}

export default function Inbox() {
  const [items, setItems] = useState<InboxItem[]>(() => {
    try {
      const cached = localStorage.getItem(CACHE_KEY)
      return cached ? (JSON.parse(cached) as InboxItem[]) : []
    } catch {
      return []
    }
  })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get<{ items: InboxItem[] }>('/inbox')
      .then((res) => {
        const fetched = res.items ?? []
        setItems(fetched)
        localStorage.setItem(CACHE_KEY, JSON.stringify(fetched))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function handleDismiss(id: string) {
    setItems((prev) => prev.filter((i) => i.id !== id))
    api.delete(`/inbox/items/${id}`).catch(() => {})
  }

  async function handleConvert(item: InboxItem) {
    await api.post('/tasks', {
      title: item.title,
      source: item.source,
      source_ref: item.source_ref,
      description: item.preview,
    })
    handleDismiss(item.id)
  }

  const kinds = Array.from(new Set(items.map((i) => i.kind)))

  return (
    <div data-testid="inbox-page" className="flex flex-col h-full">
      <TopBar title="Inbox" />
      <div className="flex-1 overflow-y-auto p-6">
        {loading && items.length === 0 ? (
          <p className="text-slate-400 text-sm">Loading...</p>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-slate-400">
            <Icon name="inbox" className="text-4xl" />
            <p className="text-sm">Your inbox is clear.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            {kinds.map((kind) => (
              <div key={kind}>
                <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2 px-1">
                  {kindLabel(kind)}
                </h2>
                <div className="flex flex-col gap-2">
                  {items
                    .filter((i) => i.kind === kind)
                    .map((item) => (
                      <div
                        key={item.id}
                        data-testid={`inbox-item-${item.id}`}
                        className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-start gap-4"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-sm font-medium text-slate-100 truncate">
                              {item.title}
                            </span>
                            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400 shrink-0">
                              {sourceLabel(item.source)}
                            </span>
                          </div>
                          {item.preview && (
                            <p className="text-xs text-slate-400">
                              {item.preview.length > 120
                                ? `${item.preview.slice(0, 120)}...`
                                : item.preview}
                            </p>
                          )}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            type="button"
                            disabled={!item.permalink}
                            onClick={() =>
                              item.permalink &&
                              window.open(item.permalink, '_blank')
                            }
                            className="text-xs px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                          >
                            Open
                          </button>
                          <button
                            type="button"
                            onClick={() => { void handleConvert(item) }}
                            className="text-xs px-3 py-1.5 rounded-lg bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 transition-colors"
                          >
                            Convert to task
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDismiss(item.id)}
                            className="text-xs px-3 py-1.5 rounded-lg bg-slate-800 text-slate-400 hover:bg-slate-700 transition-colors"
                          >
                            Dismiss
                          </button>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
