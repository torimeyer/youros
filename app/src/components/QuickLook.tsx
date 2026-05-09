import { useEffect, useState } from 'react'
import { renderMarkdown } from '../lib/markdown'

// ---------------------------------------------------------------------------
// Drive preview types (consolidated here from deleted DrivePreview.tsx)
// ---------------------------------------------------------------------------

export type DrivePreviewKind = 'image' | 'pdf' | 'sheet' | 'slides' | 'doc' | 'other'

export interface SheetTab {
  name: string
  headers: string[]
  rows: string[][]
  truncated: boolean
}

export interface SheetSample {
  sheets: SheetTab[]
}

export interface DocBlock {
  type: 'heading' | 'paragraph'
  text: string
}

export interface DocSample {
  html?: string
  blocks?: DocBlock[]
  truncated: boolean
}

export interface SlidesSample {
  slides: { slide_id: string; thumbnail_url: string }[]
  truncated: boolean
}

export interface DrivePreviewData {
  kind: DrivePreviewKind
  name: string
  mime_type: string
  thumbnail_url: string | null
  export_url: string
  web_view_link: string
  sample: SheetSample | DocSample | SlidesSample | null
}

// ---------------------------------------------------------------------------
// Local-file types
// ---------------------------------------------------------------------------

export interface QuickLookProps {
  filePath: string
  fileType: string
  onClose: () => void
  isOpen: boolean
  driveFileId?: string
  driveData?: DrivePreviewData
}

type ViewKind = 'image' | 'pdf' | 'markdown' | 'code' | 'sheet' | 'slides' | 'docx' | 'unknown'

interface SheetData {
  name: string
  rows: (string | number | boolean | null)[][]
  total_rows: number
  truncated: boolean
}
interface SlideData { index: number; title: string; body: string }
type PreviewPayload =
  | { kind: 'spreadsheet'; sheets: SheetData[] }
  | { kind: 'slides'; slides: SlideData[] }
  | { kind: 'docx'; html: string }

const IMAGE_MIME = new Set([
  'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml',
])
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])
const CODE_EXT = new Set([
  'py', 'ts', 'tsx', 'js', 'jsx', 'sh', 'css', 'html', 'json', 'yml', 'yaml', 'toml',
])
const SHEET_EXT = new Set(['xlsx', 'xlsm', 'csv'])

function fileExt(path: string): string {
  return (path.split('.').pop() ?? '').toLowerCase()
}

function basename(path: string): string {
  return path.split('/').pop() ?? path
}

function classify(fileType: string, filePath: string): ViewKind {
  const e = fileExt(filePath)
  if (IMAGE_MIME.has(fileType) || IMAGE_EXT.has(e)) return 'image'
  if (fileType === 'application/pdf' || e === 'pdf') return 'pdf'
  if (fileType === 'text/markdown' || e === 'md') return 'markdown'
  if (SHEET_EXT.has(e)) return 'sheet'
  if (e === 'pptx') return 'slides'
  if (e === 'docx') return 'docx'
  if (CODE_EXT.has(e) || fileType.startsWith('text/')) return 'code'
  return 'unknown'
}

const PREVIEW_KINDS: ViewKind[] = ['sheet', 'slides', 'docx']
const TEXT_KINDS: ViewKind[] = ['markdown', 'code']

export default function QuickLook({ filePath, fileType, onClose, isOpen, driveFileId, driveData }: QuickLookProps) {
  // Local mode state
  const [textContent, setTextContent] = useState<string | null>(null)
  const [previewData, setPreviewData] = useState<PreviewPayload | null>(null)
  const [fetchError, setFetchError] = useState(false)
  const [activeSheet, setActiveSheet] = useState(0)
  const [activeSlide, setActiveSlide] = useState(0)
  const [pdfLoadError, setPdfLoadError] = useState(false)
  const [openingNative, setOpeningNative] = useState(false)
  const [imageBlobUrl, setImageBlobUrl] = useState<string | null>(null)
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null)

  // Drive mode state
  const [drivePreviewData, setDrivePreviewData] = useState<DrivePreviewData | null>(driveData ?? null)
  const [driveLoading, setDriveLoading] = useState(!driveData && !!driveFileId)
  const [driveError, setDriveError] = useState<string | null>(null)
  const [driveSaveStatus, setDriveSaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle')
  const [driveSaveMessage, setDriveSaveMessage] = useState<string | null>(null)
  const [driveImageBlobUrl, setDriveImageBlobUrl] = useState<string | null>(null)
  const [drivePdfBlobUrl, setDrivePdfBlobUrl] = useState<string | null>(null)
  const [driveFullImage, setDriveFullImage] = useState(false)

  const isDriveMode = !!driveFileId
  const kind = isDriveMode ? null : classify(fileType, filePath)
  const rawUrl = `/api/files/raw?path=${encodeURIComponent(filePath)}`
  const previewUrl = `/api/files/preview?path=${encodeURIComponent(filePath)}`
  const name = isDriveMode ? (drivePreviewData?.name ?? '') : basename(filePath)

  // Local mode: fetch text content
  useEffect(() => {
    if (!isOpen || isDriveMode || !kind || !TEXT_KINDS.includes(kind)) return
    setTextContent(null)
    setFetchError(false)
    fetch(rawUrl)
      .then((r) => { if (!r.ok) throw new Error('fetch failed'); return r.text() })
      .then(setTextContent)
      .catch(() => setFetchError(true))
  }, [isOpen, filePath, kind, rawUrl, isDriveMode])

  // Local mode: fetch structured preview
  useEffect(() => {
    if (!isOpen || isDriveMode || !kind || !PREVIEW_KINDS.includes(kind)) return
    setPreviewData(null)
    setFetchError(false)
    setActiveSheet(0)
    setActiveSlide(0)
    fetch(previewUrl)
      .then((r) => { if (!r.ok) throw new Error('fetch failed'); return r.json() })
      .then((data: PreviewPayload) => setPreviewData(data))
      .catch(() => setFetchError(true))
  }, [isOpen, filePath, kind, previewUrl, isDriveMode])

  // Local mode: image blob URL
  useEffect(() => {
    if (!isOpen || isDriveMode || kind !== 'image') return
    let revoked = false
    let blobUrl: string | null = null
    setImageBlobUrl(null)
    setFetchError(false)
    fetch(rawUrl)
      .then(r => { if (!r.ok) throw new Error('fetch failed'); return r.blob() })
      .then(blob => {
        if (!revoked) {
          blobUrl = URL.createObjectURL(blob)
          setImageBlobUrl(blobUrl)
        }
      })
      .catch(() => setFetchError(true))
    return () => {
      revoked = true
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [isOpen, filePath, kind, rawUrl, isDriveMode])

  // Local mode: PDF blob URL
  useEffect(() => {
    if (!isOpen || isDriveMode || kind !== 'pdf') return
    let revoked = false
    let blobUrl: string | null = null
    setPdfBlobUrl(null)
    setPdfLoadError(false)
    fetch(rawUrl)
      .then(r => { if (!r.ok) throw new Error('fetch failed'); return r.blob() })
      .then(blob => {
        if (!revoked) {
          blobUrl = URL.createObjectURL(blob)
          setPdfBlobUrl(blobUrl)
        }
      })
      .catch(() => setPdfLoadError(true))
    return () => {
      revoked = true
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [isOpen, filePath, kind, rawUrl, isDriveMode])

  // Drive mode: initialize from prop when opened
  useEffect(() => {
    if (!isOpen || !driveFileId) return
    if (driveData) {
      setDrivePreviewData(driveData)
      setDriveLoading(false)
    }
  }, [isOpen, driveFileId, driveData])

  // Drive mode: fetch preview data when not pre-provided
  useEffect(() => {
    if (!isOpen || !driveFileId || !!driveData) return
    let cancelled = false
    setDrivePreviewData(null)
    setDriveLoading(true)
    setDriveError(null)
    fetch(`/api/drive/preview/${encodeURIComponent(driveFileId)}`)
      .then(r => { if (!r.ok) throw new Error(`Could not load preview (${r.status}).`); return r.json() })
      .then((d: DrivePreviewData) => {
        if (!cancelled) { setDrivePreviewData(d); setDriveLoading(false) }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setDriveError(err instanceof Error ? err.message : 'Could not load preview.')
          setDriveLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [isOpen, driveFileId, driveData])

  // Drive mode: image blob URL from export_url
  useEffect(() => {
    if (!isOpen || !drivePreviewData || drivePreviewData.kind !== 'image') return
    let revoked = false
    let blobUrl: string | null = null
    setDriveImageBlobUrl(null)
    fetch(drivePreviewData.export_url)
      .then(r => { if (!r.ok) throw new Error('fetch failed'); return r.blob() })
      .then(blob => {
        if (!revoked) {
          blobUrl = URL.createObjectURL(blob)
          setDriveImageBlobUrl(blobUrl)
        }
      })
      .catch(() => setDriveError('Could not load image.'))
    return () => {
      revoked = true
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [isOpen, drivePreviewData])

  // Drive mode: PDF blob URL from export_url
  useEffect(() => {
    if (!isOpen || !drivePreviewData || drivePreviewData.kind !== 'pdf') return
    let revoked = false
    let blobUrl: string | null = null
    setDrivePdfBlobUrl(null)
    fetch(drivePreviewData.export_url)
      .then(r => { if (!r.ok) throw new Error('fetch failed'); return r.blob() })
      .then(blob => {
        if (!revoked) {
          blobUrl = URL.createObjectURL(blob)
          setDrivePdfBlobUrl(blobUrl)
        }
      })
      .catch(() => setPdfLoadError(true))
    return () => {
      revoked = true
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [isOpen, drivePreviewData])

  // Reset all state when modal closes
  useEffect(() => {
    if (!isOpen) {
      setTextContent(null)
      setPreviewData(null)
      setFetchError(false)
      setOpeningNative(false)
      setImageBlobUrl(null)
      setPdfBlobUrl(null)
      setActiveSheet(0)
      setActiveSlide(0)
      setDrivePreviewData(null)
      setDriveLoading(false)
      setDriveError(null)
      setDriveSaveStatus('idle')
      setDriveSaveMessage(null)
      setDriveImageBlobUrl(null)
      setDrivePdfBlobUrl(null)
      setDriveFullImage(false)
    }
  }, [isOpen])

  // Keyboard handler
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        if (driveFullImage) {
          setDriveFullImage(false)
        } else {
          onClose()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose, driveFullImage])

  if (!isOpen) return null

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  const handleOpenNative = async () => {
    setOpeningNative(true)
    try {
      await fetch(`/api/files/open?path=${encodeURIComponent(filePath)}`, { method: 'POST' })
    } finally {
      setOpeningNative(false)
    }
  }

  const handleSaveToMyOS = async () => {
    if (!driveFileId) return
    setDriveSaveStatus('saving')
    setDriveSaveMessage(null)
    try {
      const resp = await fetch('/api/files/import-from-drive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ drive_file_id: driveFileId }),
      })
      const json = await resp.json()
      if (!resp.ok) throw new Error(json.detail ?? 'Import failed.')
      setDriveSaveStatus('success')
      setDriveSaveMessage('Saved to myOS.')
    } catch (err: unknown) {
      setDriveSaveStatus('error')
      setDriveSaveMessage(err instanceof Error ? err.message : 'Could not save.')
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Preview: ${name}`}
      data-testid="quicklook-modal"
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col w-full max-w-4xl max-h-[90vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 shrink-0">
          <span className="text-white font-medium text-sm truncate">{name}</span>
          <div className="flex items-center gap-2 ml-4 shrink-0">
            {isDriveMode ? (
              <>
                <button
                  data-testid="drive-preview-save-to-myos"
                  onClick={handleSaveToMyOS}
                  disabled={driveSaveStatus === 'saving' || driveSaveStatus === 'success'}
                  className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                    driveSaveStatus === 'success'
                      ? 'bg-green-500/20 border-green-500/40 text-green-300 cursor-default'
                      : driveSaveStatus === 'error'
                      ? 'bg-red-500/20 border-red-500/40 text-red-300 hover:bg-red-500/30'
                      : 'bg-blue-600/80 hover:bg-blue-600 border-blue-500/50 text-white disabled:opacity-50 disabled:cursor-not-allowed'
                  }`}
                >
                  {driveSaveStatus === 'saving' ? 'Saving...' : driveSaveStatus === 'success' ? 'Saved' : 'Save to myOS'}
                </button>
                {drivePreviewData?.web_view_link && (
                  <a
                    href={drivePreviewData.web_view_link}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg border border-slate-700 hover:border-slate-500"
                  >
                    Open in Drive
                  </a>
                )}
              </>
            ) : (
              <button
                data-testid="quicklook-open-native"
                onClick={handleOpenNative}
                disabled={openingNative}
                className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg border border-slate-700 hover:border-slate-500 disabled:opacity-50"
              >
                {openingNative ? 'Opening…' : 'Open in app'}
              </button>
            )}
            <button
              data-testid="quicklook-close"
              onClick={onClose}
              className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-slate-800"
              aria-label="Close preview"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Drive mode save error banner */}
        {isDriveMode && driveSaveStatus === 'error' && driveSaveMessage && (
          <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/20 text-xs text-red-300">
            {driveSaveMessage}
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-auto min-h-0 p-4">
          {isDriveMode ? (
            <>
              {driveLoading && (
                <p className="text-slate-500 text-sm">Loading…</p>
              )}
              {!driveLoading && driveError && (
                <p className="text-red-400 text-sm">{driveError}</p>
              )}
              {!driveLoading && !driveError && drivePreviewData && (
                <>
                  {drivePreviewData.kind === 'image' && (
                    <div className="flex items-center justify-center h-full min-h-[200px]">
                      {driveImageBlobUrl === null && (
                        <p className="text-slate-500 text-sm">Loading…</p>
                      )}
                      {driveImageBlobUrl !== null && (
                        <button
                          onClick={() => setDriveFullImage(true)}
                          className="block focus:outline-none focus:ring-2 focus:ring-blue-500 rounded-lg"
                          title="Click to view full size"
                        >
                          <img
                            src={driveImageBlobUrl}
                            alt={drivePreviewData.name}
                            className="max-w-full max-h-[70vh] object-contain cursor-zoom-in"
                            data-testid="quicklook-image"
                          />
                        </button>
                      )}
                    </div>
                  )}

                  {drivePreviewData.kind === 'pdf' && (
                    drivePdfBlobUrl === null ? (
                      <p className="text-slate-500 text-sm">Loading…</p>
                    ) : (
                      <iframe
                        src={drivePdfBlobUrl}
                        title={drivePreviewData.name}
                        className="w-full h-[70vh] border-0 rounded"
                        data-testid="quicklook-pdf"
                        onError={() => setPdfLoadError(true)}
                      />
                    )
                  )}

                  {drivePreviewData.kind === 'sheet' && (() => {
                    const sample = drivePreviewData.sample as SheetSample | null
                    const sheets = sample?.sheets ?? []
                    const sheet = sheets[activeSheet] ?? sheets[0]
                    return (
                      <div data-testid="quicklook-sheet" className="flex flex-col gap-2 h-full">
                        {sheets.length > 1 && (
                          <div className="flex gap-1 shrink-0 overflow-x-auto">
                            {sheets.map((s, i) => (
                              <button
                                key={s.name}
                                data-testid={`sheet-tab-${s.name}`}
                                onClick={() => setActiveSheet(i)}
                                className={`text-xs px-3 py-1.5 rounded border shrink-0 transition-colors ${
                                  i === activeSheet
                                    ? 'bg-slate-700 border-slate-500 text-white'
                                    : 'border-slate-700 text-slate-400 hover:text-white'
                                }`}
                              >
                                {s.name}
                              </button>
                            ))}
                          </div>
                        )}
                        {sheet && (
                          <div className="overflow-auto rounded border border-slate-700">
                            <table className="text-xs text-slate-300 w-full border-collapse">
                              {sheet.headers.length > 0 && (
                                <thead>
                                  <tr className="bg-slate-800">
                                    {sheet.headers.map((h, ci) => (
                                      <th key={ci} className="px-3 py-2 text-left font-medium text-slate-200 border-b border-slate-700 whitespace-nowrap">
                                        {h || `Column ${ci + 1}`}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                              )}
                              <tbody>
                                {sheet.rows.map((row, ri) => (
                                  <tr key={ri} className="border-b border-slate-800 hover:bg-slate-800/50">
                                    {row.map((cell, ci) => (
                                      <td key={ci} className="px-3 py-1.5 whitespace-nowrap max-w-[200px] truncate">
                                        {String(cell ?? '')}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                        {sheet?.truncated && (
                          <p className="text-xs text-slate-500 shrink-0">
                            Showing the first rows. Open in Drive to see everything.
                          </p>
                        )}
                      </div>
                    )
                  })()}

                  {drivePreviewData.kind === 'slides' && (() => {
                    const sample = drivePreviewData.sample as SlidesSample | null
                    const slides = sample?.slides ?? []
                    const current = slides[activeSlide] ?? slides[0]
                    return (
                      <div data-testid="quicklook-slides" className="flex flex-col gap-3">
                        <div className="flex items-center justify-between">
                          <button
                            data-testid="slide-prev"
                            onClick={() => setActiveSlide((i) => Math.max(0, i - 1))}
                            disabled={activeSlide === 0}
                            className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
                          >
                            <span className="material-symbols-outlined text-[20px]">chevron_left</span>
                          </button>
                          <span className="text-xs text-slate-500">Slide {activeSlide + 1} of {slides.length}</span>
                          <button
                            data-testid="slide-next"
                            onClick={() => setActiveSlide((i) => Math.min(slides.length - 1, i + 1))}
                            disabled={activeSlide === slides.length - 1}
                            className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
                          >
                            <span className="material-symbols-outlined text-[20px]">chevron_right</span>
                          </button>
                        </div>
                        {current && (
                          <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden shadow">
                            <img
                              src={current.thumbnail_url}
                              alt={`Slide ${activeSlide + 1}`}
                              className="w-full h-auto object-cover"
                            />
                          </div>
                        )}
                        {sample?.truncated && (
                          <p className="text-xs text-slate-500">
                            Showing the first slides. Open in Drive to see the full deck.
                          </p>
                        )}
                      </div>
                    )
                  })()}

                  {drivePreviewData.kind === 'doc' && (() => {
                    const sample = drivePreviewData.sample as DocSample | null
                    if (!sample) {
                      return (
                        <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                          <p className="text-slate-400 text-sm">This doc is empty.</p>
                          {drivePreviewData.web_view_link && (
                            <a href={drivePreviewData.web_view_link} target="_blank" rel="noreferrer" className="text-xs text-slate-400 hover:text-white underline">
                              Open in Drive
                            </a>
                          )}
                        </div>
                      )
                    }
                    if (sample.html) {
                      return (
                        <div data-testid="quicklook-doc" className="p-4 overflow-auto">
                          <div
                            className="bg-white rounded-lg p-6 text-gray-900 text-sm leading-relaxed max-w-3xl mx-auto"
                            dangerouslySetInnerHTML={{ __html: sample.html }}
                          />
                          {sample.truncated && (
                            <p className="mt-4 text-xs text-slate-500 max-w-3xl mx-auto">
                              Showing the first part of the doc. Open in Drive to read the rest.
                            </p>
                          )}
                        </div>
                      )
                    }
                    const blocks = sample.blocks ?? []
                    if (blocks.length === 0) {
                      return (
                        <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                          <p className="text-slate-400 text-sm">This doc is empty.</p>
                          {drivePreviewData.web_view_link && (
                            <a href={drivePreviewData.web_view_link} target="_blank" rel="noreferrer" className="text-xs text-slate-400 hover:text-white underline">
                              Open in Drive
                            </a>
                          )}
                        </div>
                      )
                    }
                    return (
                      <div data-testid="quicklook-doc" className="p-4 text-slate-200 max-w-3xl mx-auto">
                        {blocks.map((b, i) => {
                          if (b.type === 'heading') {
                            return (
                              <h2 key={i} className="text-lg font-bold text-slate-100 mt-4 mb-2 first:mt-0">
                                {b.text}
                              </h2>
                            )
                          }
                          return (
                            <p key={i} className="text-sm leading-relaxed text-slate-300 mb-3 whitespace-pre-wrap">
                              {b.text}
                            </p>
                          )
                        })}
                        {sample.truncated && (
                          <p className="mt-4 text-xs text-slate-500">
                            Showing the first part of the doc. Open in Drive to read the rest.
                          </p>
                        )}
                      </div>
                    )
                  })()}

                  {drivePreviewData.kind === 'other' && (
                    <div
                      className="flex flex-col items-center justify-center gap-4 py-12 text-center"
                      data-testid="quicklook-unknown"
                    >
                      <p className="text-slate-400 text-sm">This file type cannot be previewed here.</p>
                      {drivePreviewData.web_view_link && (
                        <a
                          href={drivePreviewData.web_view_link}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-slate-400 hover:text-white underline"
                        >
                          Open in Google Drive
                        </a>
                      )}
                    </div>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              {kind === 'image' && (
                <div className="flex items-center justify-center h-full min-h-[200px]">
                  {imageBlobUrl === null && !fetchError && (
                    <p className="text-slate-500 text-sm">Loading…</p>
                  )}
                  {fetchError && (
                    <p className="text-red-400 text-sm">Could not load this image.</p>
                  )}
                  {imageBlobUrl !== null && (
                    <img
                      src={imageBlobUrl}
                      alt={name}
                      className="max-w-full max-h-[70vh] object-contain"
                      data-testid="quicklook-image"
                    />
                  )}
                </div>
              )}

              {kind === 'pdf' && (
                pdfLoadError ? (
                  <div className="flex flex-col items-center gap-3 py-12 text-center">
                    <p className="text-slate-400 text-sm">Could not load PDF in browser.</p>
                    <button onClick={handleOpenNative} className="text-xs text-slate-400 hover:text-white underline">
                      Open in native app
                    </button>
                  </div>
                ) : pdfBlobUrl === null ? (
                  <p className="text-slate-500 text-sm">Loading…</p>
                ) : (
                  <iframe
                    src={pdfBlobUrl}
                    title={name}
                    className="w-full h-[70vh] border-0 rounded"
                    data-testid="quicklook-pdf"
                    onError={() => setPdfLoadError(true)}
                  />
                )
              )}

              {kind === 'markdown' && (
                <div
                  className="prose prose-invert max-w-none text-sm text-slate-300"
                  data-testid="quicklook-markdown"
                >
                  {textContent === null && !fetchError && <p className="text-slate-500">Loading…</p>}
                  {fetchError && <p className="text-red-400">Could not load this file.</p>}
                  {textContent !== null && renderMarkdown(textContent)}
                </div>
              )}

              {kind === 'code' && (
                <div data-testid="quicklook-code">
                  {textContent === null && !fetchError && <p className="text-slate-500 text-sm">Loading…</p>}
                  {fetchError && <p className="text-red-400 text-sm">Could not load this file.</p>}
                  {textContent !== null && (
                    <pre className="text-sm font-mono text-slate-300 whitespace-pre overflow-x-auto bg-slate-950 rounded-lg p-4">
                      <code>{textContent}</code>
                    </pre>
                  )}
                </div>
              )}

              {kind === 'sheet' && (
                <div data-testid="quicklook-sheet">
                  {previewData === null && !fetchError && <p className="text-slate-500 text-sm">Loading…</p>}
                  {fetchError && (
                    <div className="flex flex-col items-center gap-3 py-12 text-center">
                      <p className="text-red-400 text-sm">Could not load preview.</p>
                      <button onClick={handleOpenNative} className="text-xs text-slate-400 hover:text-white underline">
                        Open in native app
                      </button>
                    </div>
                  )}
                  {previewData?.kind === 'spreadsheet' && (() => {
                    const sheets = previewData.sheets
                    const sheet = sheets[activeSheet] ?? sheets[0]
                    const headers = sheet?.rows[0] ?? []
                    const dataRows = sheet?.rows.slice(1) ?? []
                    return (
                      <div className="flex flex-col gap-2 h-full">
                        {sheets.length > 1 && (
                          <div className="flex gap-1 shrink-0 overflow-x-auto">
                            {sheets.map((s, i) => (
                              <button
                                key={s.name}
                                data-testid={`sheet-tab-${s.name}`}
                                onClick={() => setActiveSheet(i)}
                                className={`text-xs px-3 py-1.5 rounded border shrink-0 transition-colors ${
                                  i === activeSheet
                                    ? 'bg-slate-700 border-slate-500 text-white'
                                    : 'border-slate-700 text-slate-400 hover:text-white'
                                }`}
                              >
                                {s.name}
                              </button>
                            ))}
                          </div>
                        )}
                        <div className="overflow-auto rounded border border-slate-700">
                          <table className="text-xs text-slate-300 w-full border-collapse">
                            {headers.length > 0 && (
                              <thead>
                                <tr className="bg-slate-800">
                                  {headers.map((h, ci) => (
                                    <th key={ci} className="px-3 py-2 text-left font-medium text-slate-200 border-b border-slate-700 whitespace-nowrap">
                                      {String(h ?? '')}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                            )}
                            <tbody>
                              {dataRows.map((row, ri) => (
                                <tr key={ri} className="border-b border-slate-800 hover:bg-slate-800/50">
                                  {row.map((cell, ci) => (
                                    <td key={ci} className="px-3 py-1.5 whitespace-nowrap max-w-[200px] truncate">
                                      {String(cell ?? '')}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        {sheet?.truncated && (
                          <p className="text-xs text-slate-500 shrink-0">
                            Showing first {sheet.rows.length} of {sheet.total_rows} rows.
                          </p>
                        )}
                      </div>
                    )
                  })()}
                </div>
              )}

              {kind === 'slides' && (
                <div data-testid="quicklook-slides">
                  {previewData === null && !fetchError && <p className="text-slate-500 text-sm">Loading…</p>}
                  {fetchError && (
                    <div className="flex flex-col items-center gap-3 py-12 text-center">
                      <p className="text-red-400 text-sm">Could not load preview.</p>
                      <button onClick={handleOpenNative} className="text-xs text-slate-400 hover:text-white underline">
                        Open in native app
                      </button>
                    </div>
                  )}
                  {previewData?.kind === 'slides' && (() => {
                    const slides = previewData.slides
                    const current = slides[activeSlide] ?? slides[0]
                    return (
                      <div className="flex flex-col gap-3">
                        <div className="flex items-center justify-between">
                          <button
                            data-testid="slide-prev"
                            onClick={() => setActiveSlide((i) => Math.max(0, i - 1))}
                            disabled={activeSlide === 0}
                            className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
                          >
                            <span className="material-symbols-outlined text-[20px]">chevron_left</span>
                          </button>
                          <span className="text-xs text-slate-500">Slide {activeSlide + 1} of {slides.length}</span>
                          <button
                            data-testid="slide-next"
                            onClick={() => setActiveSlide((i) => Math.min(slides.length - 1, i + 1))}
                            disabled={activeSlide === slides.length - 1}
                            className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
                          >
                            <span className="material-symbols-outlined text-[20px]">chevron_right</span>
                          </button>
                        </div>
                        {current && (
                          <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-4 min-h-[120px]">
                            {current.title && (
                              <div className="text-sm font-semibold text-white mb-2">{current.title}</div>
                            )}
                            {current.body && (
                              <div className="text-xs text-slate-400 whitespace-pre-wrap">{current.body}</div>
                            )}
                            {!current.title && !current.body && (
                              <div className="text-xs text-slate-600 italic">No text on this slide</div>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })()}
                </div>
              )}

              {kind === 'docx' && (
                <div data-testid="quicklook-docx">
                  {previewData === null && !fetchError && <p className="text-slate-500 text-sm">Loading…</p>}
                  {fetchError && (
                    <div className="flex flex-col items-center gap-3 py-12 text-center">
                      <p className="text-red-400 text-sm">Could not load preview.</p>
                      <button onClick={handleOpenNative} className="text-xs text-slate-400 hover:text-white underline">
                        Open in native app
                      </button>
                    </div>
                  )}
                  {previewData?.kind === 'docx' && (
                    <div
                      className="prose prose-invert max-w-none text-sm text-slate-300"
                      dangerouslySetInnerHTML={{ __html: previewData.html }}
                    />
                  )}
                </div>
              )}

              {kind === 'unknown' && (
                <div
                  className="flex flex-col items-center justify-center gap-4 py-12 text-center"
                  data-testid="quicklook-unknown"
                >
                  <p className="text-slate-400 text-sm">Preview not available for this file type.</p>
                  <button
                    onClick={handleOpenNative}
                    disabled={openingNative}
                    data-testid="quicklook-open-native-fallback"
                    className="text-xs text-slate-400 hover:text-white underline disabled:opacity-50"
                  >
                    {openingNative ? 'Opening…' : 'Open in native app'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Drive mode: full image lightbox */}
      {driveFullImage && driveImageBlobUrl && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/90 p-6"
          role="dialog"
          aria-label="Full size image"
          data-testid="drive-preview-full-image"
          onClick={() => setDriveFullImage(false)}
        >
          <img
            src={driveImageBlobUrl}
            alt={drivePreviewData?.name ?? ''}
            className="max-w-full max-h-full object-contain"
          />
          <button
            onClick={(e) => { e.stopPropagation(); setDriveFullImage(false) }}
            className="absolute top-6 right-6 w-10 h-10 rounded-full bg-slate-800/80 text-white hover:bg-slate-700 flex items-center justify-center"
            aria-label="Close full image"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}
