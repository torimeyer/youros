import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import DrivePreview from '../components/DrivePreview'
import FilePreviewPane from '../components/FilePreviewPane'
import { api } from '../lib/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// FCP Document types
interface Document {
  id: string
  name: string
  type: string
  path: string
  created: string
  updated: string
}

interface DocumentDetail extends Document {
  digest?: string
}

interface EditResult {
  digest: string
  mutations_applied: number
}

interface SaveResult {
  path: string
  size_bytes: number
  version: number
}

interface VersionEntry {
  version: number
  timestamp: string
  path: string
}

interface EditLogEntry {
  instruction: string
  success: boolean
  error?: string
  timestamp: number
}

// Preview types returned by GET /api/documents/{id}/preview
interface SheetData {
  name: string
  headers: string[]
  rows: (string | number | null)[][]
}

interface PdfPage {
  page: number
  text: string
  thumbnail?: string
}

interface SlideData {
  number: number
  title: string
  body: string
  notes?: string
}

type PreviewResponse =
  | { type: 'sheet'; sheets: SheetData[]; fallback?: false }
  | { type: 'pdf'; page_count: number; pages: PdfPage[]; fallback?: false }
  | { type: 'drawio'; xml?: string; svg?: string; fallback?: false }
  | { type: 'slides'; slides: SlideData[]; fallback?: false }
  | { fallback: true; message: string; type?: string }

// Drive types
interface DriveAuthStatus {
  authenticated: boolean
  email: string | null
  credentials_file_present: boolean
  needs_reauth: boolean
}

interface DriveFile {
  id: string
  name: string
  mimeType: string
  modifiedTime: string
  iconLink: string
  webViewLink: string
  size: string | null
}

interface DriveFilesResponse {
  files: DriveFile[]
  cached: boolean
}

interface DriveSyncResponse {
  ok: boolean
  file_count: number
  synced_at: number
}

// Files/Uploads types
interface RecentDoc {
  name: string
  path: string
  size: number
  size_display: string
  last_modified: string | null
  snippet: string
}

interface RecentDocsResponse {
  files: RecentDoc[]
}

interface BrowseEntry {
  name: string
  kind: 'folder' | 'file'
  path: string
  item_count: number | null
  size: number | null
  size_display: string
  last_modified: string | null
}

// ---------------------------------------------------------------------------
// Tab type
// ---------------------------------------------------------------------------

type TabId = 'drive' | 'uploads' | 'generated'

const TAB_META: { id: TabId; label: string; icon: string }[] = [
  { id: 'drive', label: 'My Drive', icon: 'cloud' },
  { id: 'uploads', label: 'Uploads', icon: 'upload_file' },
  { id: 'generated', label: 'Generated', icon: 'article' },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'yesterday'
  return `${days}d ago`
}

const DOC_TYPE_CONFIG: Record<string, { icon: string; color: string; label: string; groupLabel: string }> = {
  sheet: { icon: 'table_chart', color: '#4ade80', label: 'Spreadsheet', groupLabel: 'Sheets' },
  slides: { icon: 'slideshow', color: '#fb923c', label: 'Slide Deck', groupLabel: 'Slides' },
  diagram: { icon: 'account_tree', color: '#a78bfa', label: 'Diagram', groupLabel: 'Diagrams' },
  pdf: { icon: 'picture_as_pdf', color: '#f87171', label: 'PDF', groupLabel: 'PDFs' },
}

function getTypeConfig(type: string) {
  return DOC_TYPE_CONFIG[type] ?? { icon: 'description', color: '#94a3b8', label: type, groupLabel: type }
}

const MIME_ICONS: Record<string, { icon: string; color: string }> = {
  'application/vnd.google-apps.document': { icon: 'description', color: '#60a5fa' },
  'application/vnd.google-apps.presentation': { icon: 'slideshow', color: '#fb923c' },
  'application/vnd.google-apps.spreadsheet': { icon: 'table_chart', color: '#4ade80' },
  'application/vnd.google-apps.drawing': { icon: 'brush', color: '#a78bfa' },
  'application/vnd.google-apps.folder': { icon: 'folder', color: '#fbbf24' },
  'application/pdf': { icon: 'picture_as_pdf', color: '#f87171' },
}

function mimeIcon(mimeType: string): { icon: string; color: string } {
  return MIME_ICONS[mimeType] ?? { icon: 'insert_drive_file', color: '#94a3b8' }
}

function isInlinePreviewable(mimeType: string): boolean {
  return (
    mimeType === 'application/vnd.google-apps.document' ||
    mimeType === 'application/vnd.google-apps.presentation' ||
    mimeType === 'application/vnd.google-apps.spreadsheet' ||
    mimeType === 'application/vnd.google-apps.drawing' ||
    mimeType === 'application/pdf' ||
    mimeType.startsWith('image/')
  )
}

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    ts: 'code', tsx: 'code', js: 'javascript', jsx: 'javascript', py: 'code',
    rs: 'memory', go: 'speed', md: 'article', json: 'data_object',
    toml: 'settings', yaml: 'settings', yml: 'settings', html: 'web',
    css: 'palette', svg: 'image', png: 'image', jpg: 'image', jpeg: 'image',
    gif: 'image', txt: 'description', sh: 'terminal',
  }
  return map[ext] || 'description'
}

// Drive localStorage cache
const DRIVE_CACHE_KEY = 'myos.driveCache.v1'

function readDriveCache(): DriveFile[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(DRIVE_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as DriveFile[]) : []
  } catch {
    return []
  }
}

function writeDriveCache(files: DriveFile[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(DRIVE_CACHE_KEY, JSON.stringify(files))
  } catch {
    // Quota or serialization errors are not fatal.
  }
}

// ---------------------------------------------------------------------------
// Inline styles (dark theme)
// ---------------------------------------------------------------------------

// Tailwind class helpers (replaces the old `s` inline-style object)
const tw = {
  container: 'flex text-slate-200 bg-[#0b0d10]' as const,
  leftRail: 'w-[280px] min-w-[280px] border-r border-slate-800 flex flex-col overflow-hidden' as const,
  tabBar: 'flex gap-0.5 px-3 pt-3 border-b border-slate-800' as const,
  tab: (active: boolean) =>
    `flex-1 flex items-center justify-center gap-1 px-1.5 py-2 rounded-t-lg border-none text-xs font-medium cursor-pointer transition-colors
    ${active ? 'bg-slate-800 text-slate-200 font-semibold border-b-2 border-blue-500' : 'bg-transparent text-slate-500 border-b-2 border-transparent'}`,
  newBtn: 'flex items-center justify-center gap-1 mx-3 my-2 px-3 py-2 rounded-lg border-none bg-blue-500 text-white text-[13px] font-semibold cursor-pointer' as const,
  listArea: 'flex-1 overflow-y-auto px-2 pb-2' as const,
  listItem: (selected: boolean) =>
    `flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer relative transition-colors
    ${selected ? 'bg-slate-800' : 'bg-transparent hover:bg-slate-800/30'}`,
  listItemName: 'text-[13px] font-medium text-slate-200 overflow-hidden text-ellipsis whitespace-nowrap' as const,
  listItemMeta: 'text-[11px] text-slate-500 mt-0.5' as const,
  groupLabel: 'text-[11px] font-bold text-slate-500 uppercase tracking-[0.06em] px-2 pt-3 pb-1' as const,
  center: 'flex-1 flex flex-col overflow-hidden min-w-0' as const,
  centerTopBar: 'flex items-center gap-3 px-5 py-3 border-b border-slate-800 bg-[#0f1115] shrink-0' as const,
  centerContent: 'flex-1 overflow-y-auto p-5' as const,
  actionBtn: (disabled: boolean) =>
    `flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-slate-700 bg-slate-800 text-[13px] font-medium cursor-pointer
    ${disabled ? 'text-slate-600 opacity-60 cursor-not-allowed' : 'text-slate-200'}`,
  rightRail: 'w-[350px] min-w-[350px] border-l border-slate-800 flex flex-col overflow-hidden' as const,
  rightHeader: 'p-4 text-sm font-semibold text-slate-400 border-b border-slate-800' as const,
  editLog: 'flex-1 overflow-y-auto p-3 flex flex-col gap-2' as const,
  editEntry: (success: boolean) =>
    `px-3 py-2.5 rounded-lg text-[13px] border
    ${success ? 'bg-slate-900 border-slate-800' : 'bg-[#1c1113] border-red-900'}`,
  editInputBar: 'flex gap-2 p-3 border-t border-slate-800' as const,
  editInput: 'flex-1 px-3 py-2.5 rounded-lg border border-slate-700 bg-[#111827] text-slate-200 text-sm outline-none' as const,
  sendBtn: (disabled: boolean) =>
    `px-4 py-2.5 rounded-lg border-none text-[13px] font-semibold
    ${disabled ? 'bg-slate-800 text-slate-600 cursor-not-allowed' : 'bg-blue-500 text-white cursor-pointer'}`,
  emptyCenter: 'flex flex-col items-center justify-center gap-3 text-slate-500 p-12' as const,
  overlay: 'fixed inset-0 bg-black/60 flex items-center justify-center z-[1000]' as const,
  modal: 'bg-[#111827] border border-slate-800 rounded-2xl p-6 min-w-[380px] max-w-[460px] flex flex-col gap-4' as const,
  typeGrid: 'grid grid-cols-2 gap-2.5' as const,
  typeBtn: 'flex flex-col items-center gap-2 px-3 py-4 rounded-[10px] border border-slate-700 bg-slate-900 text-slate-200 cursor-pointer text-[13px] font-medium transition-colors hover:border-slate-500' as const,
  nameInput: 'w-full px-3.5 py-2.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-200 text-sm outline-none box-border' as const,
  searchInput: 'w-full px-3 py-2 rounded-lg border border-slate-700 bg-[#111827] text-slate-200 text-[13px] outline-none box-border' as const,
  dropZone: (isDragging: boolean) =>
    `flex flex-col items-center justify-center gap-2 p-8 m-5 border-2 border-dashed rounded-xl text-slate-500 cursor-pointer transition-colors
    ${isDragging ? 'border-blue-500 bg-blue-500/[0.08]' : 'border-[#444] bg-transparent'}`,
  versionsDropdown: 'absolute top-full right-0 mt-1 bg-[#111827] border border-slate-800 rounded-lg min-w-[220px] max-h-[200px] overflow-y-auto z-[100] shadow-[0_8px_24px_rgba(0,0,0,0.4)]' as const,
  kebabBtn: 'absolute right-1 top-1/2 -translate-y-1/2 bg-transparent border-none text-slate-500 cursor-pointer px-1 py-0.5 rounded text-base' as const,
  kebabMenu: 'absolute right-0 top-full bg-[#111827] border border-slate-800 rounded-lg z-[200] shadow-[0_4px_16px_rgba(0,0,0,0.4)] min-w-[120px]' as const,
}

// ---------------------------------------------------------------------------
// Preview sub-components (from original Documents.tsx)
// ---------------------------------------------------------------------------

function SheetViewer({ data }: { data: { sheets: SheetData[] } }) {
  const [activeSheet, setActiveSheet] = useState(0)
  const sheet = data.sheets[activeSheet]
  if (!sheet) return null
  const MAX_ROWS = 100
  const capped = sheet.rows.length > MAX_ROWS
  const displayRows = capped ? sheet.rows.slice(0, MAX_ROWS) : sheet.rows

  return (
    <div>
      {data.sheets.length > 1 && (
        <div data-testid="sheet-tabs" className="flex gap-0.5 mb-3">
          {data.sheets.map((sh, i) => (
            <button
              key={i}
              onClick={() => setActiveSheet(i)}
              className={`px-3.5 py-1.5 rounded-t-md border text-xs font-semibold cursor-pointer transition-colors ${i === activeSheet ? 'border-slate-700 border-b-2 border-b-blue-500 bg-slate-800 text-slate-200' : 'border-slate-700 bg-slate-900 text-slate-500'}`}
            >
              {sh.name}
            </button>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table data-testid="sheet-table" className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className="px-2.5 py-2 bg-slate-900 text-slate-500 font-bold text-left border-b-2 border-slate-700 text-[11px] w-10">#</th>
              {sheet.headers.map((h, i) => (
                <th key={i} className="px-2.5 py-2 bg-slate-900 text-slate-200 font-bold text-left border-b-2 border-slate-700">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row, ri) => (
              <tr key={ri}>
                <td className={`px-2.5 py-1.5 text-slate-500 text-[11px] font-mono border-b border-slate-800 ${ri % 2 === 0 ? 'bg-slate-950/50' : 'bg-slate-900/50'}`}>{ri + 1}</td>
                {row.map((cell, ci) => (
                  <td key={ci} className={`px-2.5 py-1.5 text-slate-200 border-b border-slate-800 ${ri % 2 === 0 ? 'bg-slate-950/50' : 'bg-slate-900/50'} ${typeof cell === 'number' ? 'font-mono' : ''}`}>{cell ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {capped && (
        <div data-testid="row-cap-note" className="mt-2.5 text-xs text-slate-500 text-center">
          Showing first {MAX_ROWS} of {sheet.rows.length} rows
        </div>
      )}
    </div>
  )
}

function PdfViewer({ data }: { data: { page_count: number; pages: PdfPage[] } }) {
  const hasThumbnails = data.pages.some((p) => p.thumbnail)
  return (
    <div data-testid="pdf-viewer">
      <div className="text-xs text-slate-500 mb-3">
        {data.page_count} {data.page_count === 1 ? 'page' : 'pages'}
      </div>
      <div className="flex flex-col gap-5">
        {data.pages.map((page) => (
          <div key={page.page}>
            <div className="text-xs font-semibold text-slate-400 mb-2 text-center">
              Page {page.page}
            </div>
            {hasThumbnails && page.thumbnail ? (
              <div className="flex justify-center bg-slate-900 rounded-lg p-3">
                <img src={page.thumbnail} alt={`Page ${page.page}`} className="max-w-full rounded border border-slate-800" />
              </div>
            ) : (
              <div className="bg-slate-900 rounded-lg p-4 text-[13px] text-slate-300 leading-relaxed whitespace-pre-wrap border border-slate-800">
                {page.text}
              </div>
            )}
            <div className="border-b border-slate-800 mt-4" />
          </div>
        ))}
      </div>
    </div>
  )
}

function DrawioViewer({ data }: { data: { xml?: string; svg?: string } }) {
  return (
    <div data-testid="drawio-viewer">
      {data.svg ? (
        <div className="flex flex-col items-center gap-3">
          <div className="max-w-[700px] w-full bg-slate-900 rounded-lg p-4 border border-slate-800" dangerouslySetInnerHTML={{ __html: data.svg }} />
          <div className="text-xs text-slate-500">Open in draw.io for full editing</div>
        </div>
      ) : data.xml ? (
        <div className="flex flex-col gap-3">
          <pre className="bg-slate-900 rounded-lg p-4 text-xs text-slate-400 overflow-auto max-h-[500px] border border-slate-800 font-mono leading-relaxed">
            {data.xml}
          </pre>
          <div className="text-xs text-slate-500">Open in draw.io for full editing</div>
        </div>
      ) : (
        <div className="text-slate-500 text-[13px]">No diagram data available</div>
      )}
    </div>
  )
}

function SlidesViewer({ data }: { data: { slides: SlideData[] } }) {
  const [activeSlide, setActiveSlide] = useState<number | null>(null)
  return (
    <div data-testid="slides-viewer" className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
      {data.slides.map((slide) => (
        <div
          key={slide.number}
          data-testid={`slide-card-${slide.number}`}
          onClick={() => setActiveSlide(activeSlide === slide.number ? null : slide.number)}
          className={`bg-slate-900 rounded-[10px] p-4 cursor-pointer border transition-all ${activeSlide === slide.number ? 'border-blue-500 shadow-[0_0_0_1px_#3b82f6]' : 'border-slate-800'}`}
        >
          <div className="text-[11px] text-slate-500 mb-1.5">Slide {slide.number}</div>
          <div className="text-[15px] font-bold text-slate-50 mb-2">{slide.title}</div>
          <div className={`text-[13px] text-slate-300 leading-relaxed ${slide.notes ? 'mb-2.5' : ''}`}>{slide.body}</div>
          {slide.notes && (
            <div className="text-xs text-slate-500 italic border-t border-slate-800 pt-2">{slide.notes}</div>
          )}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function Documents() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const initialTab: TabId = tabParam === 'drive' ? 'drive' : tabParam === 'uploads' ? 'uploads' : 'generated'
  const [activeTab, setActiveTab] = useState<TabId>(initialTab)

  // ------- Generated tab state (FCP documents) -------
  const [documents, setDocuments] = useState<Document[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [_selectedDetail, setSelectedDetail] = useState<DocumentDetail | null>(null)
  const [docLoading, setDocLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [showNewModal, setShowNewModal] = useState(false)
  const [newType, setNewType] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  const [editLog, setEditLog] = useState<EditLogEntry[]>([])
  const [editInput, setEditInput] = useState('')
  const [editSending, setEditSending] = useState(false)

  const [saving, setSaving] = useState(false)
  const [opening, setOpening] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [versions, setVersions] = useState<VersionEntry[] | null>(null)
  const [showVersions, setShowVersions] = useState(false)

  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(false)

  const [kebabDocId, setKebabDocId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const editLogRef = useRef<HTMLDivElement>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)

  // ------- My Drive tab state -------
  const [driveAuth, setDriveAuth] = useState<DriveAuthStatus | null>(null)
  const [driveFiles, setDriveFiles] = useState<DriveFile[]>(() => readDriveCache())
  const [driveLoading, setDriveLoading] = useState(false)
  const [driveSearch, setDriveSearch] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [drivePreviewFile, setDrivePreviewFile] = useState<DriveFile | null>(null)
  const [driveUploading, setDriveUploading] = useState(false)
  const driveUploadRef = useRef<HTMLInputElement | null>(null)

  // ------- Uploads tab state -------
  const [recentDocs, setRecentDocs] = useState<RecentDoc[]>([])
  const [recentLoading, setRecentLoading] = useState(true)
  const [uploadDragging, setUploadDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const uploadInputRef = useRef<HTMLInputElement | null>(null)
  const [uploadPreviewEntry, setUploadPreviewEntry] = useState<BrowseEntry | null>(null)

  // ---------- Tab switching ----------
  const switchTab = useCallback((tab: TabId) => {
    setActiveTab(tab)
    setSearchParams({ tab }, { replace: true })
  }, [setSearchParams])

  // ---------- Generated tab: data fetching ----------
  const fetchDocuments = useCallback(async () => {
    try {
      setDocLoading(true)
      const res = await api.get<Document[]>('/documents')
      setDocuments(res)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load documents. Check that the backend is running.')
    } finally {
      setDocLoading(false)
    }
  }, [])

  useEffect(() => { fetchDocuments() }, [fetchDocuments])

  // Fetch detail when selection changes
  useEffect(() => {
    if (!selectedId) { setSelectedDetail(null); return }
    let cancelled = false
    ;(async () => {
      try {
        const detail = await api.get<DocumentDetail>(`/documents/${selectedId}`)
        if (!cancelled) setSelectedDetail(detail)
      } catch { /* ignore */ }
    })()
    return () => { cancelled = true }
  }, [selectedId])

  // Fetch preview when selection changes
  const fetchPreview = useCallback(async (docId: string) => {
    setPreviewLoading(true)
    setPreviewError(false)
    setPreview(null)
    try {
      const res = await api.get<PreviewResponse>(`/documents/${docId}/preview`)
      setPreview(res)
    } catch {
      setPreviewError(true)
    } finally {
      setPreviewLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) { setPreview(null); setPreviewError(false); setPreviewLoading(false); return }
    fetchPreview(selectedId)
  }, [selectedId, fetchPreview])

  useEffect(() => {
    if (editLogRef.current) editLogRef.current.scrollTop = editLogRef.current.scrollHeight
  }, [editLog])

  useEffect(() => {
    if (newType && nameInputRef.current) nameInputRef.current.focus()
  }, [newType])

  useEffect(() => {
    setEditLog([]); setEditInput(''); setShowVersions(false); setVersions(null)
  }, [selectedId])

  const [createError, setCreateError] = useState<string | null>(null)

  // Generated tab actions
  const handleCreate = async () => {
    if (!newType || !newName.trim() || creating) return
    setCreateError(null)
    try {
      setCreating(true)
      const doc = await api.post<Document>('/documents', { type: newType, name: newName.trim() })
      setDocuments((prev) => [...prev, doc])
      setSelectedId(doc.id)
      switchTab('generated')
      setShowNewModal(false); setNewType(null); setNewName('')
    } catch (err) {
      // Show error inside the modal so the user can see it without closing
      setCreateError(err instanceof Error ? err.message : 'Could not create document.')
    } finally { setCreating(false) }
  }

  const handleEdit = async () => {
    if (!selectedId || !editInput.trim() || editSending) return
    const instruction = editInput.trim()
    setEditInput('')
    setEditSending(true)
    try {
      const res = await api.post<EditResult>(`/documents/${selectedId}/edit`, { instruction })
      setEditLog((prev) => [...prev, { instruction, success: true, timestamp: Date.now() }])
      if (res.digest) setSelectedDetail((prev) => prev ? { ...prev, digest: res.digest } : prev)
      fetchPreview(selectedId)
    } catch (err) {
      setEditLog((prev) => [...prev, { instruction, success: false, error: err instanceof Error ? err.message : 'Edit failed.', timestamp: Date.now() }])
    } finally { setEditSending(false) }
  }

  const handleSave = async () => {
    if (!selectedId || saving) return
    setSaving(true)
    try { await api.post<SaveResult>(`/documents/${selectedId}/save`) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not save.') }
    finally { setSaving(false) }
  }

  const handleOpenNative = async () => {
    if (!selectedId || opening) return
    setOpening(true)
    try { await api.post(`/documents/${selectedId}/open-native`) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not open in app.') }
    finally { setOpening(false) }
  }

  const handleDownload = () => {
    if (!selectedId || downloading) return
    setDownloading(true)
    const link = document.createElement('a')
    link.href = `/api/documents/${selectedId}/download`
    link.download = ''
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => setDownloading(false), 1000)
  }

  const handleVersionsToggle = async () => {
    if (showVersions) { setShowVersions(false); return }
    if (!selectedId) return
    try {
      const res = await api.get<VersionEntry[]>(`/documents/${selectedId}/versions`)
      setVersions(res); setShowVersions(true)
    } catch { /* ignore */ }
  }

  const handleDelete = async (docId: string) => {
    if (deleting) return
    setDeleting(true)
    try {
      await api.delete(`/documents/${docId}`)
      setDocuments((prev) => prev.filter((d) => d.id !== docId))
      if (selectedId === docId) { setSelectedId(null); setSelectedDetail(null) }
      setKebabDocId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete document.')
    } finally { setDeleting(false) }
  }

  // ---------- Drive tab: data fetching ----------
  const fetchDriveStatus = useCallback(async () => {
    try {
      const status = await api.get<DriveAuthStatus>('/drive/auth/status')
      setDriveAuth(status)
      return status
    } catch {
      setDriveAuth({ authenticated: false, email: null, credentials_file_present: false, needs_reauth: false })
      return null
    }
  }, [])

  const fetchDriveFiles = useCallback(async (q?: string) => {
    setDriveLoading(true)
    try {
      const path = q ? `/drive/files?q=${encodeURIComponent(q)}` : '/drive/files'
      const res = await api.get<DriveFilesResponse>(path)
      const list = res.files ?? []
      setDriveFiles(list)
      if (!q && list.length > 0) writeDriveCache(list)
    } catch {
      // ignore
    } finally { setDriveLoading(false) }
  }, [])

  // Check drive auth on mount
  useEffect(() => {
    const hasCached = readDriveCache().length > 0
    if (hasCached) {
      const statusPromise = fetchDriveStatus()
      const filesPromise = fetchDriveFiles()
      statusPromise.then((status) => {
        if (!status?.authenticated) return
        return filesPromise
      })
      return
    }
    fetchDriveStatus().then((status) => {
      if (status?.authenticated) fetchDriveFiles()
    })
  }, [fetchDriveStatus, fetchDriveFiles])

  const handleDriveConnect = async () => {
    try {
      const { url } = await api.get<{ url: string }>('/drive/auth/url')
      window.open(url, '_blank')
    } catch { /* ignore */ }
  }

  const handleDriveSync = async () => {
    setSyncing(true)
    try {
      await api.post<DriveSyncResponse>('/drive/sync')
      await fetchDriveFiles(driveSearch || undefined)
    } catch { /* ignore */ }
    finally { setSyncing(false) }
  }

  const handleDriveUpload = async (file: File) => {
    setDriveUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      await fetch('/api/drive/files/upload', { method: 'POST', body: formData })
      await fetchDriveFiles(driveSearch || undefined)
    } catch { /* ignore */ }
    finally {
      setDriveUploading(false)
      if (driveUploadRef.current) driveUploadRef.current.value = ''
    }
  }

  // Filter drive files by search
  const filteredDriveFiles = driveSearch.trim()
    ? driveFiles.filter((f) => f.name.toLowerCase().includes(driveSearch.trim().toLowerCase()))
    : driveFiles

  // ---------- Uploads tab: data fetching ----------
  const fetchRecentDocs = useCallback(async () => {
    setRecentLoading(true)
    try {
      const res = await api.get<RecentDocsResponse>('/docs/recent?limit=20')
      setRecentDocs(res.files ?? [])
    } catch { setRecentDocs([]) }
    finally { setRecentLoading(false) }
  }, [])

  useEffect(() => { fetchRecentDocs() }, [fetchRecentDocs])

  const handleLocalUpload = async (file: File) => {
    setUploading(true)
    try {
      // Auto-detect type from extension
      const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
      const typeMap: Record<string, string> = {
        xlsx: 'sheet', xls: 'sheet', csv: 'sheet',
        pptx: 'slides', ppt: 'slides',
        drawio: 'drawio',
        pdf: 'pdf',
      }
      const docType = typeMap[ext]
      if (docType) {
        // Upload as an FCP document
        const formData = new FormData()
        formData.append('file', file)
        formData.append('type', docType)
        formData.append('name', file.name)
        await fetch('/api/documents/upload', { method: 'POST', body: formData })
      }
      // Always refresh
      await fetchRecentDocs()
    } catch { /* ignore */ }
    finally { setUploading(false) }
  }

  const handleUploadDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setUploadDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleLocalUpload(file)
  }

  // ---------- Grouped docs for Generated tab ----------
  const groupOrder = ['sheet', 'slides', 'diagram', 'pdf']
  const grouped = groupOrder.reduce<Record<string, Document[]>>((acc, type) => {
    const docs = documents.filter((d) => d.type === type)
    if (docs.length > 0) acc[type] = docs
    return acc
  }, {})
  const knownTypes = new Set(groupOrder)
  const other = documents.filter((d) => !knownTypes.has(d.type))
  if (other.length > 0) grouped['other'] = other

  const selectedDoc = documents.find((d) => d.id === selectedId) ?? null

  // ---------- Preview renderer for Generated tab ----------
  function renderGeneratedPreview() {
    if (previewLoading) {
      return (
        <div data-testid="preview-loading" className="flex items-center justify-center gap-2.5 p-12 text-slate-500">
          <Icon name="progress_activity" size={20} className="text-slate-500" />
          <span className="text-sm">Loading preview...</span>
        </div>
      )
    }
    if (previewError) {
      return (
        <div data-testid="preview-error" className="flex flex-col items-center gap-3 p-12">
          <div className="text-sm text-slate-400">Could not load preview. The file might still be generating.</div>
          <button data-testid="retry-preview" onClick={() => selectedId && fetchPreview(selectedId)} className="px-4 py-2 rounded-lg border border-slate-700 bg-slate-800 text-slate-200 text-[13px] font-medium cursor-pointer">Retry</button>
        </div>
      )
    }
    if (!preview) return null
    if ('fallback' in preview && preview.fallback) {
      return (
        <div data-testid="preview-fallback" className="p-8">
          <div className="text-sm text-amber-400 mb-2">{preview.message}</div>
          <div className="text-xs text-slate-500">You may need to install additional software to preview this file type.</div>
        </div>
      )
    }
    if ('type' in preview) {
      switch (preview.type) {
        case 'sheet': return <SheetViewer data={preview} />
        case 'pdf': return <PdfViewer data={preview} />
        case 'drawio': return <DrawioViewer data={preview} />
        case 'slides': return <SlidesViewer data={preview} />
        default: return null
      }
    }
    return null
  }

  // Should we show the right rail (edit panel)? Only for Generated tab when a doc is selected.
  const showRightRail = activeTab === 'generated' && selectedDoc !== null

  return (
    <>
      <TopBar title="Documents" />

      {error && (
        <div data-testid="error-banner" style={{ padding: '10px 16px', background: '#1c1113', borderBottom: '1px solid #7f1d1d', color: '#fca5a5', fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>{error}</span>
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', fontSize: 16 }}>
            <Icon name="close" size={16} />
          </button>
        </div>
      )}

      <div data-testid="documents-page" className={tw.container} style={{ height: 'calc(100vh - 64px)' }}>
        {/* ============ Left rail ============ */}
        <div data-testid="left-rail" className={tw.leftRail}>
          {/* Tab bar */}
          <div data-testid="tab-bar" className={tw.tabBar}>
            {TAB_META.map((t) => (
              <button
                key={t.id}
                data-testid={`tab-${t.id}`}
                onClick={() => switchTab(t.id)}
                className={tw.tab(activeTab === t.id)}
              >
                <Icon name={t.icon} size={14} />
                {t.label}
              </button>
            ))}
          </div>

          {/* New button (shows for Generated tab) */}
          {activeTab === 'generated' && (
            <button data-testid="new-button" onClick={() => setShowNewModal(true)} disabled={creating} className={tw.newBtn}>
              <Icon name="add" size={16} /> New
            </button>
          )}

          {/* ---- My Drive tab list ---- */}
          {activeTab === 'drive' && (
            <div className={tw.listArea}>
              {driveAuth === null && (
                <div className={tw.emptyCenter} style={{ padding: 24 }}>
                  <Icon name="progress_activity" size={20} style={{ color: '#64748b' }} />
                  <span style={{ fontSize: 13 }}>Checking connection...</span>
                </div>
              )}

              {driveAuth && !driveAuth.authenticated && (
                <div style={{ padding: 16, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                  <Icon name="cloud_off" size={32} style={{ color: '#475569' }} />
                  <span style={{ fontSize: 14, fontWeight: 500, color: '#94a3b8', textAlign: 'center' }}>Connect Google Drive to see your files here</span>
                  <button data-testid="connect-drive-button" onClick={handleDriveConnect} className={`${tw.newBtn} w-full`}>
                    <Icon name="login" size={16} /> Connect Google Drive
                  </button>
                </div>
              )}

              {driveAuth?.authenticated && (
                <>
                  {driveAuth.email && (
                    <div style={{ padding: '6px 12px', fontSize: 11, color: '#64748b', display: 'flex', alignItems: 'center', gap: 4 }}>
                      <Icon name="account_circle" size={14} />
                      {driveAuth.email}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 4, padding: '4px 8px' }}>
                    <button onClick={handleDriveSync} disabled={syncing} className={`${tw.actionBtn(syncing)} flex-1 justify-center`} style={{ padding: '6px 8px', fontSize: 12 }}>
                      <Icon name="sync" size={14} /> {syncing ? 'Syncing...' : 'Sync'}
                    </button>
                    <input ref={driveUploadRef} type="file" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) handleDriveUpload(f) }} />
                    <button onClick={() => driveUploadRef.current?.click()} disabled={driveUploading} className={`${tw.actionBtn(driveUploading)} flex-1 justify-center`} style={{ padding: '6px 8px', fontSize: 12 }}>
                      <Icon name="upload" size={14} /> {driveUploading ? 'Uploading...' : 'Upload'}
                    </button>
                  </div>
                  <div style={{ padding: '4px 12px' }}>
                    <input
                      data-testid="drive-search"
                      type="text"
                      value={driveSearch}
                      onChange={(e) => setDriveSearch(e.target.value)}
                      placeholder="Search files..."
                      className={tw.searchInput}
                    />
                  </div>
                  {driveLoading && driveFiles.length === 0 && (
                    <div className={tw.emptyCenter} style={{ padding: 24 }}>
                      <Icon name="progress_activity" size={20} style={{ color: '#64748b' }} />
                      <span style={{ fontSize: 13 }}>Loading files...</span>
                    </div>
                  )}
                  {filteredDriveFiles.map((file) => {
                    const { icon, color } = mimeIcon(file.mimeType)
                    return (
                      <div
                        key={file.id}
                        data-testid={`drive-file-${file.id}`}
                        className={tw.listItem(drivePreviewFile?.id === file.id)}
                        onClick={() => {
                          if (isInlinePreviewable(file.mimeType)) {
                            setDrivePreviewFile(file)
                          } else if (file.webViewLink) {
                            window.open(file.webViewLink, '_blank', 'noopener,noreferrer')
                          }
                        }}
                      >
                        <Icon name={icon} size={18} style={{ color, flexShrink: 0 }} />
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div className={tw.listItemName}>{file.name}</div>
                          <div className={tw.listItemMeta}>{timeAgo(file.modifiedTime)}</div>
                        </div>
                      </div>
                    )
                  })}
                </>
              )}
            </div>
          )}

          {/* ---- Uploads tab list ---- */}
          {activeTab === 'uploads' && (
            <div className={tw.listArea}>
              {/* Drop zone */}
              <div
                data-testid="upload-drop-zone"
                onDragOver={(e) => { e.preventDefault(); setUploadDragging(true) }}
                onDragLeave={() => setUploadDragging(false)}
                onDrop={handleUploadDrop}
                onClick={() => uploadInputRef.current?.click()}
                className={tw.dropZone(uploadDragging)}
              >
                <input ref={uploadInputRef} type="file" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) handleLocalUpload(f) }} />
                {uploading ? (
                  <>
                    <Icon name="progress_activity" size={24} style={{ color: '#64748b' }} />
                    <span style={{ fontSize: 13 }}>Uploading...</span>
                  </>
                ) : (
                  <>
                    <Icon name="upload_file" size={28} style={{ color: '#475569' }} />
                    <span style={{ fontSize: 13 }}>Drop a file here or click to upload</span>
                  </>
                )}
              </div>

              {/* Recent docs list */}
              {recentLoading && (
                <div className={tw.emptyCenter} style={{ padding: 24 }}>
                  <Icon name="progress_activity" size={20} style={{ color: '#64748b' }} />
                  <span style={{ fontSize: 13 }}>Loading...</span>
                </div>
              )}
              {!recentLoading && recentDocs.length === 0 && (
                <div className={tw.emptyCenter} style={{ padding: 24 }}>
                  <Icon name="folder_open" size={28} style={{ color: '#475569' }} />
                  <span style={{ fontSize: 13 }}>No recent files</span>
                </div>
              )}
              {recentDocs.map((doc) => (
                <div
                  key={doc.path}
                  data-testid={`upload-file-${doc.path}`}
                  className={tw.listItem(uploadPreviewEntry?.path === doc.path)}
                  onClick={() => setUploadPreviewEntry({
                    name: doc.name, kind: 'file', path: doc.path,
                    item_count: null, size: doc.size, size_display: doc.size_display, last_modified: doc.last_modified,
                  })}
                >
                  <Icon name={fileIcon(doc.name)} size={18} style={{ color: '#94a3b8', flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className={tw.listItemName}>{doc.name}</div>
                    <div className={tw.listItemMeta}>{doc.size_display} . {timeAgo(doc.last_modified)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* ---- Generated tab list ---- */}
          {activeTab === 'generated' && (
            <div className={tw.listArea}>
              {docLoading ? (
                <div className={tw.emptyCenter} style={{ padding: 24 }}>
                  <Icon name="progress_activity" size={20} style={{ color: '#64748b' }} />
                  <span style={{ fontSize: 13 }}>Loading...</span>
                </div>
              ) : documents.length === 0 ? (
                <div data-testid="empty-list" className={tw.emptyCenter}>
                  <Icon name="description" size={32} style={{ color: '#475569' }} />
                  <span style={{ fontSize: 14, fontWeight: 500 }}>No documents yet</span>
                  <span style={{ fontSize: 12 }}>Click "New" to create one</span>
                </div>
              ) : (
                Object.entries(grouped).map(([type, docs]) => {
                  const cfg = getTypeConfig(type)
                  return (
                    <div key={type}>
                      <div className={tw.groupLabel}>{cfg.groupLabel}</div>
                      {docs.map((doc) => (
                        <div
                          key={doc.id}
                          data-testid={`doc-item-${doc.id}`}
                          className={tw.listItem(selectedId === doc.id)}
                          onClick={() => setSelectedId(doc.id)}
                          onMouseEnter={(e) => { if (selectedId !== doc.id) (e.currentTarget as HTMLDivElement).style.background = '#1e293b50' }}
                          onMouseLeave={(e) => { if (selectedId !== doc.id) (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
                        >
                          <Icon name={cfg.icon} size={18} style={{ color: cfg.color, flexShrink: 0 }} />
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div className={tw.listItemName}>{doc.name}</div>
                            <div className={tw.listItemMeta}>{timeAgo(doc.updated)}</div>
                          </div>
                          <button
                            data-testid={`kebab-${doc.id}`}
                            onClick={(e) => { e.stopPropagation(); setKebabDocId(kebabDocId === doc.id ? null : doc.id) }}
                            className={tw.kebabBtn}
                          >
                            <Icon name="more_vert" size={16} />
                          </button>
                          {kebabDocId === doc.id && (
                            <div className={tw.kebabMenu}>
                              <button
                                data-testid={`delete-${doc.id}`}
                                onClick={(e) => { e.stopPropagation(); handleDelete(doc.id) }}
                                disabled={deleting}
                                style={{ padding: '8px 14px', fontSize: 13, color: '#f87171', cursor: 'pointer', border: 'none', background: 'transparent', width: '100%', textAlign: 'left' } as React.CSSProperties}
                              >
                                {deleting ? 'Removing...' : 'Remove'}
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                })
              )}
            </div>
          )}
        </div>

        {/* ============ Center pane ============ */}
        <div data-testid="center-panel" className={tw.center}>
          {/* Generated tab center */}
          {activeTab === 'generated' && (
            <>
              {!selectedDoc ? (
                <div className={`${tw.emptyCenter} flex-1 justify-center`}>
                  <Icon name="description" size={40} style={{ color: '#334155' }} />
                  <span style={{ fontSize: 15, fontWeight: 500, color: '#64748b' }}>Select a document to view details</span>
                </div>
              ) : (
                <>
                  <div className={tw.centerTopBar}>
                    <Icon name={getTypeConfig(selectedDoc.type).icon} size={22} style={{ color: getTypeConfig(selectedDoc.type).color }} />
                    <div style={{ fontSize: 16, fontWeight: 600, color: '#f1f5f9', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedDoc.name}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, position: 'relative' }}>
                      <button data-testid="save-button" onClick={handleSave} disabled={saving} className={tw.actionBtn(saving)}><Icon name="save" size={16} />{saving ? 'Saving...' : 'Save'}</button>
                      <button data-testid="download-button" onClick={handleDownload} disabled={downloading} className={tw.actionBtn(downloading)}><Icon name="download" size={16} />{downloading ? 'Downloading...' : 'Download'}</button>
                      <button data-testid="open-native-button" onClick={handleOpenNative} disabled={opening} className={tw.actionBtn(opening)}><Icon name="open_in_new" size={16} />{opening ? 'Opening...' : 'Open in app'}</button>
                      <div style={{ position: 'relative' }}>
                        <button data-testid="versions-button" onClick={handleVersionsToggle} className={tw.actionBtn(false)}><Icon name="history" size={16} />Versions<Icon name={showVersions ? 'expand_less' : 'expand_more'} size={14} /></button>
                        {showVersions && versions && (
                          <div className={tw.versionsDropdown}>
                            {versions.length === 0 ? (
                              <div style={{ padding: '12px', fontSize: 13, color: '#64748b' }}>No saved versions yet</div>
                            ) : versions.map((v) => (
                              <div key={v.version} style={{ padding: '8px 12px', fontSize: 13, color: '#e2e8f0', cursor: 'pointer', borderBottom: '1px solid #1e293b' }}>
                                <div style={{ fontWeight: 500 }}>Version {v.version}</div>
                                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{timeAgo(v.timestamp)}</div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                  <div data-testid="preview-area" className={tw.centerContent}>
                    {renderGeneratedPreview()}
                  </div>
                </>
              )}
            </>
          )}

          {/* Drive tab center */}
          {activeTab === 'drive' && (
            <>
              {drivePreviewFile ? (
                <>
                  <div className={tw.centerTopBar}>
                    <Icon name={mimeIcon(drivePreviewFile.mimeType).icon} size={22} style={{ color: mimeIcon(drivePreviewFile.mimeType).color }} />
                    <div style={{ fontSize: 16, fontWeight: 600, color: '#f1f5f9', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{drivePreviewFile.name}</div>
                    {drivePreviewFile.webViewLink && (
                      <a href={drivePreviewFile.webViewLink} target="_blank" rel="noreferrer" className={tw.actionBtn(false)} style={{ textDecoration: 'none' }}>
                        <Icon name="open_in_new" size={16} />Open in Google Drive
                      </a>
                    )}
                    <button onClick={() => setDrivePreviewFile(null)} className={tw.actionBtn(false)}>
                      <Icon name="close" size={16} />Close
                    </button>
                  </div>
                  <div className={tw.centerContent}>
                    <DrivePreview
                      fileId={drivePreviewFile.id}
                      name={drivePreviewFile.name}
                      mimeType={drivePreviewFile.mimeType}
                      webViewLink={drivePreviewFile.webViewLink}
                      onClose={() => setDrivePreviewFile(null)}
                    />
                  </div>
                </>
              ) : (
                <div className={`${tw.emptyCenter} flex-1 justify-center`}>
                  <Icon name="cloud" size={40} style={{ color: '#334155' }} />
                  <span style={{ fontSize: 15, fontWeight: 500, color: '#64748b' }}>Select a file from My Drive to preview</span>
                </div>
              )}
            </>
          )}

          {/* Uploads tab center */}
          {activeTab === 'uploads' && (
            <>
              {uploadPreviewEntry ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                  <div className={tw.centerTopBar}>
                    <Icon name={fileIcon(uploadPreviewEntry.name)} size={22} style={{ color: '#94a3b8' }} />
                    <div style={{ fontSize: 16, fontWeight: 600, color: '#f1f5f9', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{uploadPreviewEntry.name}</div>
                    <button onClick={() => setUploadPreviewEntry(null)} className={tw.actionBtn(false)}>
                      <Icon name="close" size={16} />Close
                    </button>
                  </div>
                  <div style={{ flex: 1 }}>
                    <FilePreviewPane
                      entry={uploadPreviewEntry}
                      onClose={() => setUploadPreviewEntry(null)}
                      onOpenExternally={async (path: string) => {
                        try { await api.post('/projects/open-file', { path }) } catch { /* ignore */ }
                      }}
                    />
                  </div>
                </div>
              ) : (
                <div className={`${tw.emptyCenter} flex-1 justify-center`}>
                  <Icon name="upload_file" size={40} style={{ color: '#334155' }} />
                  <span style={{ fontSize: 15, fontWeight: 500, color: '#64748b' }}>Select a file to preview, or drag and drop to upload</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* ============ Right rail (edit panel, only for Generated tab) ============ */}
        {showRightRail && (
          <div data-testid="right-rail" className={tw.rightRail}>
            <div className={tw.rightHeader}>Edit instructions</div>
            <div ref={editLogRef} data-testid="edit-log" className={tw.editLog}>
              {editLog.length === 0 && (
                <div className={`${tw.emptyCenter} text-[13px]`} style={{ padding: 24 }}>
                  {selectedDoc ? 'Type an instruction below to edit this document' : 'Select a document first'}
                </div>
              )}
              {editLog.map((entry, i) => (
                <div key={i} data-testid="edit-entry" className={tw.editEntry(entry.success)}>
                  <div style={{ color: '#e2e8f0', fontWeight: 500, marginBottom: 4 }}>{entry.instruction}</div>
                  <div style={{ color: entry.success ? '#4ade80' : '#f87171', fontSize: 12 }}>
                    {entry.success ? 'Done' : entry.error ?? 'Failed'}
                  </div>
                </div>
              ))}
            </div>
            <div className={tw.editInputBar}>
              <input
                data-testid="edit-input"
                type="text"
                placeholder={selectedDoc ? 'e.g. "add a row for March"' : 'Select a document first'}
                value={editInput}
                onChange={(e) => setEditInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleEdit() }}
                disabled={!selectedDoc || editSending}
                className={tw.editInput}
              />
              <button
                data-testid="send-edit-button"
                onClick={handleEdit}
                disabled={!selectedDoc || !editInput.trim() || editSending}
                className={tw.sendBtn(!selectedDoc || !editInput.trim() || editSending)}
              >
                {editSending ? '...' : 'Send'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* New document modal */}
      {showNewModal && (
        <div data-testid="new-doc-modal" className={tw.overlay} onClick={() => { setShowNewModal(false); setNewType(null); setNewName(''); setCreateError(null) }}>
          <div className={tw.modal} onClick={(e) => e.stopPropagation()}>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#f1f5f9' }}>
              {newType ? `Name your ${getTypeConfig(newType).label.toLowerCase()}` : 'Create a new document'}
            </div>
            {createError && (
              <div data-testid="create-error" style={{ padding: '10px 12px', background: '#1c1113', border: '1px solid #7f1d1d', borderRadius: 8, color: '#fca5a5', fontSize: 13 }}>
                {createError}
              </div>
            )}
            {!newType ? (
              <div data-testid="type-picker" className={tw.typeGrid}>
                {[
                  { type: 'sheet', label: 'Spreadsheet' },
                  { type: 'slides', label: 'Slide Deck' },
                  { type: 'diagram', label: 'Diagram' },
                  { type: 'pdf', label: 'PDF' },
                ].map(({ type, label }) => {
                  const cfg = getTypeConfig(type)
                  return (
                    <button
                      key={type}
                      data-testid={`type-${type}`}
                      onClick={() => setNewType(type)}
                      className={tw.typeBtn}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = cfg.color }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#334155' }}
                    >
                      <Icon name={cfg.icon} size={28} style={{ color: cfg.color }} />
                      {label}
                    </button>
                  )
                })}
              </div>
            ) : (
              <>
                <input
                  ref={nameInputRef}
                  data-testid="name-input"
                  type="text"
                  placeholder="Document name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleCreate() }}
                  className={tw.nameInput}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button onClick={() => { setNewType(null); setNewName('') }} style={{ padding: '8px 16px', borderRadius: 8, border: '1px solid #334155', background: 'transparent', color: '#94a3b8', fontSize: 13, cursor: 'pointer' }}>Back</button>
                  <button
                    data-testid="create-button"
                    onClick={handleCreate}
                    disabled={!newName.trim() || creating}
                    style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: (!newName.trim() || creating) ? '#1e293b' : '#3b82f6', color: (!newName.trim() || creating) ? '#475569' : '#fff', fontSize: 13, fontWeight: 600, cursor: (!newName.trim() || creating) ? 'not-allowed' : 'pointer' }}
                  >
                    {creating ? 'Creating...' : 'Create'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
