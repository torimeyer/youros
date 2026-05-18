import { useState, useEffect } from 'react'
import { api } from '../../lib/api'

interface AuditSpecItem {
  path: string
  frontmatter: { title: string; status: string }
  sections_present: string[]
  sections_missing: string[]
  score: number
  last_edited?: string
}

interface AuditResponse {
  specs: AuditSpecItem[]
  summary: { total: number; fully_templated: number; partial: number; score_avg: number }
}

function slugFromPath(path: string): string {
  return path.split('/').pop()?.replace(/\.md$/, '') ?? path
}

function locationFromPath(path: string): string {
  if (path.startsWith('~/.myos/specs/') || path.includes('~/.myos/specs/')) return '~/.myos/specs/'
  if (path.startsWith('docs/spec/') || path.includes('docs/spec/')) return 'docs/spec/'
  return path
}

function isStale(lastEdited?: string): boolean {
  if (!lastEdited) return false
  return Date.now() - new Date(lastEdited).getTime() > 90 * 24 * 60 * 60 * 1000
}

function formatDate(iso?: string): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString()
}

export default function SpecHealthTab() {
  const [data, setData] = useState<AuditResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get<AuditResponse>('/specs/audit')
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleBackfill(path: string) {
    const slug = slugFromPath(path)
    await api.post(`/specs/${slug}/backfill`, {})
    api.get<AuditResponse>('/specs/audit').then(setData).catch(() => {})
  }

  async function handleArchive(path: string) {
    const slug = slugFromPath(path)
    await api.post(`/specs/${slug}/archive`, {})
    api.get<AuditResponse>('/specs/audit').then(setData).catch(() => {})
  }

  if (loading) return <p className="text-sm text-slate-400">Loading spec health...</p>
  if (!data) return <p className="text-sm text-slate-500">No spec data available.</p>

  return (
    <div className="flex flex-col gap-4">
      {data.summary && (
        <div className="text-xs text-slate-400">
          {data.summary.total} specs · avg score {data.summary.score_avg.toFixed(1)}/10
        </div>
      )}
      <div className="flex flex-col gap-2">
        {data.specs.map((spec, i) => {
          const stale = isStale(spec.last_edited)
          return (
            <div key={i} className="rounded-lg bg-slate-800/50 px-4 py-3 flex flex-col gap-2">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-sm text-slate-200 flex-1 min-w-0 truncate">
                  {spec.frontmatter.title}
                </span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-300 shrink-0">
                  {locationFromPath(spec.path)}
                </span>
                <span className="text-xs font-mono text-slate-300 shrink-0">
                  {spec.score}/10
                </span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400 shrink-0">
                  {spec.frontmatter.status}
                </span>
                <span className="text-xs text-slate-500 shrink-0">
                  {formatDate(spec.last_edited)}
                </span>
                {stale && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-amber-900/50 text-amber-400 shrink-0">
                    Stale
                  </span>
                )}
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleBackfill(spec.path)}
                    className="text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium px-2 py-1 rounded"
                  >
                    Backfill
                  </button>
                  <button
                    onClick={() => handleArchive(spec.path)}
                    className="text-xs bg-slate-600 hover:bg-slate-500 text-white font-medium px-2 py-1 rounded"
                  >
                    Archive
                  </button>
                </div>
              </div>
              {spec.sections_missing.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {spec.sections_missing.map((s) => (
                    <span key={s} className="text-xs px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-400">
                      {s}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
