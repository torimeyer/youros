import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, SkeletonLine } from './ui'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'
import type { QueryPreset } from './queryPresets'

interface JiraRow {
  key: string
  summary: string
  status: string
  priority: string
  type: string
  updated: string
  due: string | null
  url: string
}

interface ConfluenceRow {
  id: string
  title: string
  type: string
  updated: string
  url: string
}

export function formatDueLabel(due: string, now: Date = new Date()): string {
  const d = new Date(due)
  const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dueDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.round((dueDay.getTime() - nowDay.getTime()) / (1000 * 60 * 60 * 24))
  if (diffDays === 0) return 'due today'
  if (diffDays === 1) return 'due tomorrow'
  if (diffDays > 1) return `due in ${diffDays} days`
  const n = Math.abs(diffDays)
  return `overdue ${n} day${n === 1 ? '' : 's'}`
}

export default function QueryWidget({ preset }: { preset: QueryPreset }) {
  const navigate = useNavigate()
  const [jiraRows, setJiraRows] = useState<JiraRow[]>([])
  const [confRows, setConfRows] = useState<ConfluenceRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchRows = async () => {
      try {
        setLoading(true)
        setError(null)
        if (preset.source === 'jira') {
          const res = await api.get<{ rows: JiraRow[] }>(
            `/atlassian/jira/query?jql=${encodeURIComponent(preset.query)}&limit=5`
          )
          setJiraRows(res.rows || [])
        } else {
          const res = await api.get<{ rows: ConfluenceRow[] }>(
            `/atlassian/confluence/query?cql=${encodeURIComponent(preset.query)}&limit=5`
          )
          setConfRows(res.rows || [])
        }
      } catch (err) {
        reportError('Failed to fetch query widget rows', err)
        setError('Not connected or failed to load')
      } finally {
        setLoading(false)
      }
    }

    fetchRows()
  }, [preset])

  const rowCount = preset.source === 'jira' ? jiraRows.length : confRows.length
  const isEmpty = rowCount === 0
  const cardDest = preset.source === 'jira' ? '/jira' : '/confluence'

  return (
    <div data-testid={`widget-${preset.id}`}>
      <Card hover padding="sm" className="sm:p-6" onClick={() => navigate(cardDest)}>
        <div className="flex items-center justify-between mb-4 pr-8">
          <h2 className="text-lg font-semibold">{preset.title}</h2>
          {!loading && !error && (
            <span className="text-xs text-slate-500">{rowCount}</span>
          )}
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : isEmpty ? (
          <p className="text-sm text-slate-500">{preset.emptyText}</p>
        ) : preset.source === 'jira' ? (
          <div className="space-y-2">
            {jiraRows.map((row) => (
              <div
                key={row.key}
                onClick={(e) => {
                  e.stopPropagation()
                  navigate(`/jira/${row.key}`)
                }}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer"
              >
                <span className="text-[10px] font-mono text-slate-500 shrink-0">{row.key}</span>
                <span className="flex-1 text-sm font-medium truncate">{row.summary}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
                  row.status === 'In Progress'
                    ? 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
                    : 'bg-slate-100/60 dark:bg-slate-700/60 text-slate-600 dark:text-slate-400'
                }`}>
                  {row.status}
                </span>
                {row.due && (
                  <span className="text-[10px] text-slate-500 shrink-0">
                    {formatDueLabel(row.due)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {confRows.map((row) => (
              <div
                key={row.id}
                onClick={(e) => {
                  e.stopPropagation()
                  navigate(`/confluence/${row.id}`)
                }}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer"
              >
                <span className="flex-1 text-sm font-medium truncate">{row.title}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
