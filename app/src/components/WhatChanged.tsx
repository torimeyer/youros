import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import Icon from './Icon'

interface WhatChangedData {
  completed_agents: { name: string; status: string; summary: string; completed_at: string }[]
  artifacts_created: { name: string; path: string; created_at: string }[]
  awaiting_input: { id: string; title: string }[]
  new_emails: { subject: string; from_name: string; snippet: string; date: string }[]
  calendar_events_happened: { summary: string; start: any; htmlLink: string }[]
  drive_files_changed: { name: string; mimeType: string; modifiedTime: string; webViewLink: string }[]
}

function SourceGroup({ icon, label, count, children, defaultOpen = false }: {
  icon: string; label: string; count: number; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (count === 0) return null
  return (
    <div className="border-b border-slate-800 last:border-0 pb-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full text-left py-1.5 hover:bg-slate-800/50 rounded px-1 -mx-1"
      >
        <Icon name={icon} size={14} className="text-slate-400" />
        <span className="text-sm text-slate-300">{label}</span>
        <span className="text-xs text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded ml-auto">
          {count}
        </span>
      </button>
      {open && <div className="pl-5 space-y-1 mt-1">{children}</div>}
    </div>
  )
}

export default function WhatChanged() {
  const [data, setData] = useState<WhatChangedData | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    api.get(`/since-you-last-looked?since=${twoHoursAgo}`)
      .then((res: any) => setData(res.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading || !data) return null

  const totalChanges =
    (data.new_emails?.length || 0) +
    (data.calendar_events_happened?.length || 0) +
    (data.drive_files_changed?.length || 0) +
    (data.completed_agents?.length || 0) +
    (data.artifacts_created?.length || 0)

  if (totalChanges === 0) return null

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-3">
        What changed
        <span className="text-xs text-slate-400 ml-2">last 2 hours</span>
      </h3>
      <div className="space-y-1">
        <SourceGroup icon="mail" label="New emails" count={data.new_emails?.length || 0} defaultOpen>
          {data.new_emails?.slice(0, 5).map((e, i) => (
            <div key={i} className="text-xs text-slate-400 cursor-pointer hover:text-slate-200 truncate"
              onClick={() => navigate('/gmail')}>
              <span className="text-slate-300">{e.from_name}</span>: {e.subject}
            </div>
          ))}
        </SourceGroup>

        <SourceGroup icon="calendar" label="Meetings happened" count={data.calendar_events_happened?.length || 0}>
          {data.calendar_events_happened?.map((e, i) => (
            <div key={i} className="text-xs text-slate-400 cursor-pointer hover:text-slate-200"
              onClick={() => navigate('/calendar')}>
              {e.summary}
            </div>
          ))}
        </SourceGroup>

        <SourceGroup icon="file" label="Drive files changed" count={data.drive_files_changed?.length || 0}>
          {data.drive_files_changed?.map((f, i) => (
            <a key={i} href={f.webViewLink} target="_blank" rel="noreferrer"
              className="text-xs text-slate-400 hover:text-slate-200 block truncate">
              {f.name}
            </a>
          ))}
        </SourceGroup>

        <SourceGroup icon="cpu" label="Agents finished" count={data.completed_agents?.length || 0}>
          {data.completed_agents?.slice(0, 5).map((a, i) => (
            <div key={i} className="text-xs text-slate-400 truncate">
              <span className={a.status === 'completed' ? 'text-green-400' : 'text-red-400'}>
                {a.status}
              </span>{' '}
              {a.summary || a.name}
            </div>
          ))}
        </SourceGroup>

        <SourceGroup icon="file-plus" label="Files created" count={data.artifacts_created?.length || 0}>
          {data.artifacts_created?.slice(0, 5).map((f, i) => (
            <div key={i} className="text-xs text-slate-400 truncate">{f.name}</div>
          ))}
        </SourceGroup>
      </div>
    </div>
  )
}
