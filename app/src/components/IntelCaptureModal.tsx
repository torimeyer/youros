import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import Icon from './Icon'

interface Props {
  open: boolean
  onClose: () => void
  onCaptured?: () => void
}

type InputMode = 'url' | 'text'

export default function IntelCaptureModal({ open, onClose, onCaptured }: Props) {
  const [mode, setMode] = useState<InputMode>('url')
  const [competitor, setCompetitor] = useState('')
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [tags, setTags] = useState('')
  const [competitors, setCompetitors] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    api
      .get<{ competitors: string[] }>('/intel/competitors')
      .then((r) => setCompetitors(r.competitors || []))
      .catch(() => {})
  }, [open])

  const reset = () => {
    setCompetitor('')
    setUrl('')
    setText('')
    setTags('')
    setMode('url')
    setError(null)
  }

  const handleClose = () => {
    reset()
    onClose()
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!competitor.trim()) {
      setError('Competitor name is required')
      return
    }
    const tagList = tags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)
    try {
      setSubmitting(true)
      setError(null)
      await api.post('/intel/capture', {
        competitor: competitor.trim(),
        url: mode === 'url' ? url.trim() || null : null,
        text: mode === 'text' ? text.trim() || null : null,
        tags: tagList,
      })
      reset()
      onCaptured?.()
    } catch {
      setError('Could not save — please try again')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-label="Add competitor signal"
      data-testid="intel-capture-modal"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">Add competitor signal</h2>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Competitor */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Competitor
            </label>
            <input
              type="text"
              list="intel-competitors-list"
              value={competitor}
              onChange={(e) => setCompetitor(e.target.value)}
              placeholder="Company name"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
              data-testid="intel-competitor-input"
              required
            />
            <datalist id="intel-competitors-list">
              {competitors.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>

          {/* URL / Text toggle */}
          <div>
            <div className="flex gap-3 mb-2">
              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="intel-mode"
                  value="url"
                  checked={mode === 'url'}
                  onChange={() => setMode('url')}
                  data-testid="intel-mode-url"
                />
                URL
              </label>
              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="intel-mode"
                  value="text"
                  checked={mode === 'text'}
                  onChange={() => setMode('text')}
                  data-testid="intel-mode-text"
                />
                Paste text
              </label>
            </div>

            {mode === 'url' ? (
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://competitor.com/article"
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                data-testid="intel-url-input"
              />
            ) : (
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste a quote, note, or summary…"
                rows={4}
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-violet-500 resize-none"
                data-testid="intel-text-input"
              />
            )}
          </div>

          {/* Tags */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Tags <span className="text-slate-500 font-normal">(comma-separated)</span>
            </label>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="pricing, feature, partnership"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
              data-testid="intel-tags-input"
            />
          </div>

          {error && (
            <p className="text-sm text-red-400" role="alert">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-3 pt-1">
            <button
              type="button"
              onClick={handleClose}
              className="px-4 py-2 text-sm text-slate-400 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white rounded-lg transition-colors"
              data-testid="intel-submit-btn"
            >
              {submitting ? 'Saving…' : 'Save signal'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
