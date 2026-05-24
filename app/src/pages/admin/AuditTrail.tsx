import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'
import { formatDateTime } from '../../lib/time'

interface AuditEvent {
  ts: string
  event: string
  data: Record<string, unknown>
  user?: string
}

export default function AdminAuditTrail() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-slate-900/50 border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-200' : 'text-slate-800'
  const inputCls = darkMode ? 'bg-slate-800 border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'

  useEffect(() => {
    api.get<{ events: AuditEvent[] }>('/enterprise/audit')
      .then((res) => setEvents(res.events || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const eventTypes = [...new Set(events.map((e) => e.event))].sort()

  const filtered = events.filter((e) => {
    if (typeFilter && e.event !== typeFilter) return false
    if (search) {
      const s = search.toLowerCase()
      return (
        e.event.toLowerCase().includes(s) ||
        (e.user || '').toLowerCase().includes(s) ||
        JSON.stringify(e.data).toLowerCase().includes(s)
      )
    }
    return true
  })

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `audit-trail-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className={cardCls}>
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <h2 className={`text-base font-semibold ${headingCls}`}>
            Audit trail ({filtered.length} events)
          </h2>
          <div className="flex-1" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search events..."
            className={`border rounded-lg px-3 py-1.5 text-sm w-48 focus:outline-none focus:border-indigo-500 ${inputCls}`}
          />
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className={`border rounded-lg px-2 py-1.5 text-sm ${inputCls}`}
          >
            <option value="">All types</option>
            {eventTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium team-text hover:opacity-80 transition-opacity"
          >
            <Icon name="download" size={16} />
            Export
          </button>
        </div>

        {/* Event list */}
        {filtered.length === 0 ? (
          <p className={`text-sm ${labelCls}`}>
            {search || typeFilter ? 'No matching events.' : 'No audit events yet.'}
          </p>
        ) : (
          <div className="space-y-1">
            {filtered.map((e, i) => {
              const expanded = expandedIdx === i
              return (
                <button
                  key={i}
                  onClick={() => setExpandedIdx(expanded ? null : i)}
                  className={`w-full text-left px-4 py-3 rounded-lg transition-colors ${
                    expanded
                      ? darkMode ? 'bg-slate-800' : 'bg-slate-100'
                      : darkMode ? 'hover:bg-slate-800/50' : 'hover:bg-slate-50'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon
                      name={expanded ? 'expand_less' : 'expand_more'}
                      size={16}
                      className={labelCls}
                    />
                    <span className={`text-xs font-mono ${labelCls}`}>
                      {formatDateTime(e.ts)}
                    </span>
                    <span className={`text-sm font-medium ${valueCls}`}>{e.event}</span>
                    {e.user && (
                      <span className={`text-xs ${labelCls}`}>{e.user}</span>
                    )}
                  </div>
                  {expanded && (
                    <pre className={`mt-2 ml-7 text-xs p-3 rounded-lg overflow-x-auto ${
                      darkMode ? 'bg-slate-900 text-slate-300' : 'bg-white text-slate-700'
                    }`}>
                      {JSON.stringify(e.data, null, 2)}
                    </pre>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
