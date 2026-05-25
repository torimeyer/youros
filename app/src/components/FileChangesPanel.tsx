import { useState } from 'react'
import InlineDiff from './InlineDiff'

export interface FileChange {
  path: string
  diff: string
  is_binary: boolean
  size_bytes: number
  mime_type: string
}

interface Props {
  turnId: string
  files: FileChange[]
}

export default function FileChangesPanel({ turnId, files }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [undone, setUndone] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState<Record<string, boolean>>({})

  async function undoFile(path: string) {
    setLoading(prev => ({ ...prev, [path]: true }))
    try {
      const res = await fetch('/api/turn_audit/undo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ turn_id: turnId, path }),
      })
      if (res.status === 409) {
        setErrors(prev => ({ ...prev, [path]: 'File was changed externally — undo not possible.' }))
      } else if (!res.ok) {
        setErrors(prev => ({ ...prev, [path]: 'Undo failed.' }))
      } else {
        setUndone(prev => ({ ...prev, [path]: true }))
        setErrors(prev => {
          const next = { ...prev }
          delete next[path]
          return next
        })
      }
    } catch {
      setErrors(prev => ({ ...prev, [path]: 'Undo failed.' }))
    } finally {
      setLoading(prev => ({ ...prev, [path]: false }))
    }
  }

  async function undoAll() {
    for (const file of files) {
      if (!undone[file.path]) {
        await undoFile(file.path)
      }
    }
  }

  return (
    <div className="mt-2" data-testid="file-changes-panel">
      <button
        onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors"
        data-testid="file-changes-toggle"
        aria-expanded={expanded}
      >
        <span className="font-mono">{expanded ? '▾' : '▸'}</span>
        <span>Files changed ({files.length})</span>
      </button>

      {expanded && (
        <div className="mt-1.5 space-y-2" data-testid="file-changes-expanded">
          {files.map(file => (
            <div
              key={file.path}
              className="rounded border border-slate-200 dark:border-slate-700 overflow-hidden text-xs"
              data-testid={`file-change-${file.path}`}
            >
              <div className="flex items-center justify-between gap-2 px-3 py-1.5 bg-slate-100 dark:bg-slate-800">
                <span className="font-mono text-slate-700 dark:text-slate-300 truncate">{file.path}</span>
                <div className="flex items-center gap-2 shrink-0">
                  {file.is_binary && (
                    <span className="text-slate-500 text-xs">{file.mime_type} · {file.size_bytes}B</span>
                  )}
                  {errors[file.path] && (
                    <span className="text-red-600 dark:text-red-400 text-xs" data-testid={`undo-error-${file.path}`}>
                      {errors[file.path]}
                    </span>
                  )}
                  {undone[file.path] ? (
                    <span className="text-green-600 dark:text-green-400 text-xs" data-testid={`undone-${file.path}`}>Undone</span>
                  ) : (
                    <button
                      onClick={() => undoFile(file.path)}
                      disabled={!!loading[file.path]}
                      className="px-2 py-0.5 rounded bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-700 dark:text-slate-300 transition-colors"
                      data-testid={`undo-btn-${file.path}`}
                    >
                      {loading[file.path] ? '…' : 'Undo'}
                    </button>
                  )}
                </div>
              </div>
              {!file.is_binary && file.diff && (
                <InlineDiff diff={file.diff} />
              )}
            </div>
          ))}

          {files.length > 1 && (
            <button
              onClick={undoAll}
              className="text-xs px-3 py-1 rounded bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
              data-testid="undo-all-btn"
            >
              Undo all changes
            </button>
          )}
        </div>
      )}
    </div>
  )
}
