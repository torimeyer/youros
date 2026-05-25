import { useState, useEffect } from 'react'
import Icon from './Icon'
import { Card, SkeletonLine } from './ui'
import { api } from '../lib/api'
import { formatDate } from '../lib/time'
import IntelCaptureModal from './IntelCaptureModal'

interface IntelCapture {
  id: string
  competitor: string
  tags: string[]
  url: string | null
  text: string | null
  captured_at: string
}

interface FeedResponse {
  captures: IntelCapture[]
}

function truncate(s: string, n = 60): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

export default function CompetitiveIntelWidget() {
  const [captures, setCaptures] = useState<IntelCapture[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  const load = async () => {
    try {
      setLoading(true)
      setError(null)
      const res = await api.get<FeedResponse>('/intel/feed')
      setCaptures((res.captures || []).slice(0, 5))
    } catch {
      setError('Could not load competitor signals')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleCaptured = () => {
    setModalOpen(false)
    load()
  }

  return (
    <div data-testid="widget-competitive-intel">
      <Card padding="sm" className="sm:p-6">
        <div className="flex items-center justify-between mb-4 pr-2">
          <div className="flex items-center gap-2">
            <Icon name="radar" className="text-violet-600 dark:text-violet-400" size={20} />
            <h2 className="text-lg font-semibold">Competitor Signals</h2>
          </div>
          <button
            onClick={() => setModalOpen(true)}
            className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 transition-colors flex items-center gap-1"
            aria-label="Add competitor signal"
          >
            <Icon name="add" size={14} />
            Add
          </button>
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : captures.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="intel-empty-state">
            No signals yet. Hit Add to capture something.
          </p>
        ) : (
          <div className="space-y-2">
            {captures.map((c) => (
              <div
                key={c.id}
                className="flex flex-col gap-0.5 p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors"
                data-testid="intel-capture-row"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-white truncate">
                    {c.competitor}
                  </span>
                  <span className="text-xs text-slate-500 shrink-0">
                    {formatDate(c.captured_at)}
                  </span>
                </div>
                {c.url && (
                  <span className="text-xs text-slate-600 dark:text-slate-400 truncate">
                    {truncate(c.url)}
                  </span>
                )}
                {!c.url && c.text && (
                  <span className="text-xs text-slate-600 dark:text-slate-400">
                    {truncate(c.text)}
                  </span>
                )}
                {c.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {c.tags.map((t) => (
                      <span
                        key={t}
                        className="text-xs bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded px-1.5 py-0.5"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <IntelCaptureModal open={modalOpen} onClose={() => setModalOpen(false)} onCaptured={handleCaptured} />
    </div>
  )
}
