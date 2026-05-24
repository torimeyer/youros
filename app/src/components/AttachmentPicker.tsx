import { useState, useEffect, useRef } from 'react'
import { api } from '../lib/api'
import { formatDate } from '../lib/time'

export interface AttachmentFile {
  id: string
  name: string
  path?: string
  url?: string
  mimeType?: string
  modifiedAt?: string
  size?: number
  source: 'agent' | 'upload' | 'drive'
}

interface Props {
  open: boolean
  onSelect: (file: AttachmentFile) => void
  onClose: () => void
}

type Tab = 'agents' | 'uploads' | 'drive'

const TAB_LABELS: Record<Tab, string> = {
  agents: 'Recent from Agents',
  uploads: 'My Uploads',
  drive: 'Google Drive',
}

interface TimelineFile {
  id: string
  name: string
  path: string
  size: number
  modified_at: string
  mime_type: string
  provenance: { source?: string } | null
}

interface DriveFile {
  id: string
  name: string
  mimeType: string
  modifiedTime: string
  webViewLink?: string
  size?: number
}



function FileRow({
  name,
  subtitle,
  onClick,
  testId,
}: {
  name: string
  subtitle: string
  onClick: () => void
  testId?: string
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-800 transition-colors text-left group"
    >
      <div className="w-8 h-8 rounded bg-slate-700 flex items-center justify-center shrink-0 text-slate-400 text-xs font-mono uppercase">
        {name.split('.').pop()?.slice(0, 3) ?? '?'}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-white truncate group-hover:text-pink-300 transition-colors">
          {name}
        </div>
        <div className="text-xs text-slate-500 truncate">{subtitle}</div>
      </div>
    </button>
  )
}

function TabContent({
  tab,
  onSelect,
}: {
  tab: Tab
  onSelect: (f: AttachmentFile) => void
}) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<AttachmentFile[]>([])
  const loadedRef = useRef(false)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true

    let cancelled = false
    setLoading(true)
    setError(null)

    const fetch = async () => {
      try {
        let result: AttachmentFile[] = []

        if (tab === 'agents') {
          const res = await api.get<{ files: TimelineFile[] }>('/files/timeline?limit=50')
          result = res.files.map((f) => ({
            id: f.id,
            name: f.name,
            path: f.path,
            mimeType: f.mime_type,
            modifiedAt: f.modified_at,
            size: f.size,
            source: 'agent',
          }))
        } else if (tab === 'uploads') {
          const res = await api.get<{ files: TimelineFile[] }>('/files/list?path=uploads')
          result = res.files.map((f) => ({
            id: f.id ?? f.name,
            name: f.name,
            path: f.path,
            mimeType: f.mime_type,
            modifiedAt: f.modified_at,
            size: f.size,
            source: 'upload',
          }))
        } else {
          const res = await api.get<{ files: DriveFile[] }>('/drive/files')
          result = res.files.map((f) => ({
            id: f.id,
            name: f.name,
            url: f.webViewLink,
            mimeType: f.mimeType,
            modifiedAt: f.modifiedTime,
            size: f.size,
            source: 'drive',
          }))
        }

        if (!cancelled) setFiles(result)
      } catch (err) {
        if (!cancelled) setError('Could not load files. Check your connection and try again.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetch()
    return () => { cancelled = true }
  }, [tab])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 text-sm">
        Loading...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 text-sm px-6 text-center">
        {error}
      </div>
    )
  }

  if (files.length === 0) {
    const emptyMsg: Record<Tab, string> = {
      agents: 'No recent files from agents yet.',
      uploads: 'No uploads yet.',
      drive: 'No Google Drive files found.',
    }
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 text-sm">
        {emptyMsg[tab]}
      </div>
    )
  }

  return (
    <div className="overflow-y-auto flex-1" data-testid={`tab-content-${tab}`}>
      {files.map((f, i) => (
        <FileRow
          key={f.id ?? i}
          name={f.name}
          subtitle={f.modifiedAt ? formatDate(f.modifiedAt) : ''}
          onClick={() => onSelect(f)}
          testId={`file-row-${tab}-${i}`}
        />
      ))}
    </div>
  )
}

export default function AttachmentPicker({ open, onSelect, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('agents')

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  const handleBackdrop = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Pick an attachment"
      data-testid="attachment-picker-backdrop"
      onClick={handleBackdrop}
      className="fixed inset-0 z-50 flex items-end justify-center sm:items-center bg-black/60 p-4"
    >
      <div
        data-testid="attachment-picker-modal"
        className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg flex flex-col shadow-2xl"
        style={{ maxHeight: '70vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-4 pb-0 shrink-0">
          <h2 className="text-white font-semibold text-sm">Add attachment</h2>
          <button
            type="button"
            data-testid="attachment-picker-close"
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-0 px-4 pt-3 pb-0 border-b border-slate-800 shrink-0">
          {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              data-testid={`tab-${tab}`}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? 'border-pink-500 text-pink-400'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              {TAB_LABELS[tab]}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <TabContent
            key={activeTab}
            tab={activeTab}
            onSelect={(f) => {
              onSelect(f)
              onClose()
            }}
          />
        </div>
      </div>
    </div>
  )
}
