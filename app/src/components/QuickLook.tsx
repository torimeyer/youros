import { useEffect, useState } from 'react'
import { renderMarkdown } from '../lib/markdown'

export interface QuickLookProps {
  filePath: string
  fileType: string
  onClose: () => void
  isOpen: boolean
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

export default function QuickLook({ filePath, fileType, onClose, isOpen }: QuickLookProps) {
  const [textContent, setTextContent] = useState<string | null>(null)
  const [previewData, setPreviewData] = useState<PreviewPayload | null>(null)
  const [fetchError, setFetchError] = useState(false)
  const [activeSheet, setActiveSheet] = useState(0)
  const [openingNative, setOpeningNative] = useState(false)

  const kind = classify(fileType, filePath)
  const rawUrl = `/api/files/raw?path=${encodeURIComponent(filePath)}`
  const previewUrl = `/api/files/preview?path=${encodeURIComponent(filePath)}`
  const name = basename(filePath)

  useEffect(() => {
    if (!isOpen || !TEXT_KINDS.includes(kind)) return
    setTextContent(null)
    setFetchError(false)
    fetch(rawUrl)
      .then((r) => { if (!r.ok) throw new Error('fetch failed'); return r.text() })
      .then(setTextContent)
      .catch(() => setFetchError(true))
  }, [isOpen, filePath, kind, rawUrl])

  useEffect(() => {
    if (!isOpen || !PREVIEW_KINDS.includes(kind)) return
    setPreviewData(null)
    setFetchError(false)
    setActiveSheet(0)
    fetch(previewUrl)
      .then((r) => { if (!r.ok) throw new Error('fetch failed'); return r.json() })
      .then((data: PreviewPayload) => setPreviewData(data))
      .catch(() => setFetchError(true))
  }, [isOpen, filePath, kind, previewUrl])

  useEffect(() => {
    if (!isOpen) {
      setTextContent(null)
      setPreviewData(null)
      setFetchError(false)
      setOpeningNative(false)
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

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
            <button
              data-testid="quicklook-open-native"
              onClick={handleOpenNative}
              disabled={openingNative}
              className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg border border-slate-700 hover:border-slate-500 disabled:opacity-50"
            >
              {openingNative ? 'Opening…' : 'Open in app'}
            </button>
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

        {/* Body */}
        <div className="flex-1 overflow-auto min-h-0 p-4">
          {kind === 'image' && (
            <div className="flex items-center justify-center h-full min-h-[200px]">
              <img
                src={rawUrl}
                alt={name}
                className="max-w-full max-h-[70vh] object-contain"
                data-testid="quicklook-image"
              />
            </div>
          )}

          {kind === 'pdf' && (
            <iframe
              src={rawUrl}
              title={name}
              className="w-full h-[70vh] border-0 rounded"
              data-testid="quicklook-pdf"
            />
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
              {previewData?.kind === 'slides' && (
                <div className="flex flex-col gap-3">
                  {previewData.slides.map((slide) => (
                    <div key={slide.index} className="rounded-lg border border-slate-700 bg-slate-800/50 p-4">
                      <div className="text-xs text-slate-500 mb-1">Slide {slide.index}</div>
                      {slide.title && (
                        <div className="text-sm font-semibold text-white mb-1">{slide.title}</div>
                      )}
                      {slide.body && (
                        <div className="text-xs text-slate-400 whitespace-pre-wrap">{slide.body}</div>
                      )}
                      {!slide.title && !slide.body && (
                        <div className="text-xs text-slate-600 italic">No text on this slide</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
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
        </div>
      </div>
    </div>
  )
}
