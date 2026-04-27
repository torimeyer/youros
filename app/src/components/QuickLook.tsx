import { useEffect, useState } from 'react'
import { renderMarkdown } from '../lib/markdown'

export interface QuickLookProps {
  filePath: string
  fileType: string
  onClose: () => void
  isOpen: boolean
}

type ViewKind = 'image' | 'pdf' | 'markdown' | 'code' | 'office' | 'unknown'

const IMAGE_MIME = new Set([
  'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml',
])
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])
const CODE_EXT = new Set([
  'py', 'ts', 'tsx', 'js', 'jsx', 'sh', 'css', 'html', 'json', 'yml', 'yaml', 'toml',
])
const OFFICE_EXT = new Set(['xlsx', 'pptx', 'docx'])

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
  if (CODE_EXT.has(e) || fileType.startsWith('text/')) return 'code'
  if (OFFICE_EXT.has(e)) return 'office'
  return 'unknown'
}

export default function QuickLook({ filePath, fileType, onClose, isOpen }: QuickLookProps) {
  const [textContent, setTextContent] = useState<string | null>(null)
  const [fetchError, setFetchError] = useState(false)

  const kind = classify(fileType, filePath)
  const rawUrl = `/api/files/raw?path=${encodeURIComponent(filePath)}`
  const name = basename(filePath)

  useEffect(() => {
    if (!isOpen || (kind !== 'markdown' && kind !== 'code')) return
    setTextContent(null)
    setFetchError(false)
    fetch(rawUrl)
      .then((r) => {
        if (!r.ok) throw new Error('fetch failed')
        return r.text()
      })
      .then(setTextContent)
      .catch(() => setFetchError(true))
  }, [isOpen, filePath, kind, rawUrl])

  useEffect(() => {
    if (!isOpen) {
      setTextContent(null)
      setFetchError(false)
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
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
            <a
              href={rawUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg border border-slate-700 hover:border-slate-500"
            >
              Open externally
            </a>
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
              {textContent === null && !fetchError && (
                <p className="text-slate-500">Loading...</p>
              )}
              {fetchError && (
                <p className="text-red-400">Could not load this file.</p>
              )}
              {textContent !== null && renderMarkdown(textContent)}
            </div>
          )}

          {kind === 'code' && (
            <div data-testid="quicklook-code">
              {textContent === null && !fetchError && (
                <p className="text-slate-500 text-sm">Loading...</p>
              )}
              {fetchError && (
                <p className="text-red-400 text-sm">Could not load this file.</p>
              )}
              {textContent !== null && (
                <pre className="text-sm font-mono text-slate-300 whitespace-pre overflow-x-auto bg-slate-950 rounded-lg p-4">
                  <code>{textContent}</code>
                </pre>
              )}
            </div>
          )}

          {kind === 'office' && (
            <div
              className="flex flex-col items-center justify-center gap-4 py-12 text-center"
              data-testid="quicklook-office"
            >
              <p className="text-slate-400 text-sm">
                This file type can't be previewed here. Open it in a native app to view it.
              </p>
              <a
                href={rawUrl}
                download
                className="text-xs text-slate-400 hover:text-white underline"
                data-testid="quicklook-download-link"
              >
                Download file
              </a>
            </div>
          )}

          {kind === 'unknown' && (
            <div
              className="flex flex-col items-center justify-center gap-4 py-12 text-center"
              data-testid="quicklook-unknown"
            >
              <p className="text-slate-400 text-sm">
                Preview not available for this file type.
              </p>
              <a
                href={rawUrl}
                download
                className="text-xs text-slate-400 hover:text-white underline"
                data-testid="quicklook-download-link"
              >
                Download file
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
