import React, { useState, useEffect } from 'react'
import { api } from '../lib/api'

export type RuntimeKind = 'claude' | 'gemini'

export interface OstkContextPage {
  name: string
  tier: 'hot' | 'warm' | 'cold' | string
  tokens: number
  owner: string | null
  pinned: boolean
}

interface OstkContextPagesResponse {
  available?: boolean
  pages?: OstkContextPage[]
  total_pages?: number
  total_tokens?: number
  hot_tokens?: number
}

export interface HandoffRuntimeModalProps {
  isOpen: boolean
  onClose: () => void
  agentName?: string
  taskId?: string
  currentModel?: string
  onHandedOff?: (res: unknown) => void
}

const CLAUDE_MODELS = [
  { value: 'claude-sonnet-5-5', label: 'Sonnet 5.5' },
  { value: 'claude-opus-5-5', label: 'Opus 5.5' },
  { value: 'claude-haiku-5-5', label: 'Haiku 5.5' },
  { value: 'claude-fable-5-2', label: 'Fable 5.2' },
] as const

const GEMINI_MODELS = [
  { value: 'gemini-3.1-pro', label: 'Gemini 3.1 Pro' },
  { value: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
  { value: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash Lite' },
] as const

export function inferOppositeTarget(currentModel?: string): {
  runtime: RuntimeKind
  model: string
} {
  const lower = (currentModel ?? '').toLowerCase()
  if (
    lower.includes('gemini') ||
    lower.includes('flash') ||
    (!lower.includes('claude') && lower.includes('pro'))
  ) {
    return { runtime: 'claude', model: 'claude-sonnet-5-5' }
  }
  if (
    lower.includes('claude') ||
    lower.includes('sonnet') ||
    lower.includes('opus') ||
    lower.includes('haiku') ||
    lower.includes('fable')
  ) {
    return { runtime: 'gemini', model: 'gemini-3.1-pro' }
  }
  return { runtime: 'gemini', model: 'gemini-3.1-pro' }
}

function getTierBadgeClasses(tier: string): string {
  const norm = tier.toLowerCase()
  if (norm === 'hot') {
    return 'bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30'
  }
  if (norm === 'warm') {
    return 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30'
  }
  return 'bg-slate-500/15 text-slate-600 dark:text-slate-400 border-slate-500/30'
}

export function HandoffRuntimeModal({
  isOpen,
  onClose,
  agentName,
  taskId,
  currentModel,
  onHandedOff,
}: HandoffRuntimeModalProps) {
  const initial = inferOppositeTarget(currentModel)
  const [targetRuntime, setTargetRuntime] = useState<RuntimeKind>(initial.runtime)
  const [targetModel, setTargetModel] = useState<string>(initial.model)
  const [transitionNote, setTransitionNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [contextPages, setContextPages] = useState<OstkContextPage[]>([])
  const [selectedPages, setSelectedPages] = useState<Set<string>>(new Set())
  const [busyPageAction, setBusyPageAction] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!isOpen) return
    const next = inferOppositeTarget(currentModel)
    setTargetRuntime(next.runtime)
    setTargetModel(next.model)
    setTransitionNote('')
    setError(null)
    setSubmitting(false)
    setContextPages([])
    setSelectedPages(new Set())
    setBusyPageAction({})

    let cancelled = false
    if (typeof api.get === 'function') {
      Promise.resolve(api.get<OstkContextPagesResponse>('/ostk/context-pages'))
        .then((data) => {
          if (cancelled || !data || !Array.isArray(data.pages) || data.pages.length === 0) {
            return
          }
          setContextPages(data.pages)
          const defaultHot = new Set(
            data.pages.filter((p) => p.tier === 'hot').map((p) => p.name)
          )
          setSelectedPages(defaultHot)
        })
        .catch(() => {
          // Degrade gracefully when ostk context pages are unavailable.
        })
    }
    return () => {
      cancelled = true
    }
  }, [isOpen, currentModel, agentName, taskId])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, submitting, onClose])

  if (!isOpen) return null

  const modelOptions = targetRuntime === 'claude' ? CLAUDE_MODELS : GEMINI_MODELS

  const handleRuntimeChange = (nextRuntime: RuntimeKind) => {
    setTargetRuntime(nextRuntime)
    setTargetModel(nextRuntime === 'claude' ? 'claude-sonnet-5-5' : 'gemini-3.1-pro')
  }

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && !submitting) {
      onClose()
    }
  }

  const handleTogglePage = (name: string) => {
    setSelectedPages((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const handleRestorePage = async (page: OstkContextPage) => {
    const key = `restore:${page.name}`
    if (busyPageAction[key]) return
    setBusyPageAction((prev) => ({ ...prev, [key]: true }))
    try {
      const res = (await api.post(
        `/ostk/context-pages/${encodeURIComponent(page.name)}/restore`,
        {}
      )) as { ok?: boolean } | undefined
      if (!res || res.ok !== false) {
        setContextPages((prev) =>
          prev.map((p) => (p.name === page.name ? { ...p, tier: 'hot' } : p))
        )
        setSelectedPages((prev) => {
          const next = new Set(prev)
          next.add(page.name)
          return next
        })
      }
    } catch {
      // Non-blocking inline action.
    } finally {
      setBusyPageAction((prev) => ({ ...prev, [key]: false }))
    }
  }

  const handlePinPage = async (page: OstkContextPage) => {
    const key = `pin:${page.name}`
    if (busyPageAction[key]) return
    const unpin = Boolean(page.pinned)
    setBusyPageAction((prev) => ({ ...prev, [key]: true }))
    try {
      const res = (await api.post(
        `/ostk/context-pages/${encodeURIComponent(page.name)}/pin`,
        { unpin }
      )) as { ok?: boolean; pinned?: boolean } | undefined
      if (!res || res.ok !== false) {
        const nextPinned = typeof res?.pinned === 'boolean' ? res.pinned : !unpin
        setContextPages((prev) =>
          prev.map((p) => (p.name === page.name ? { ...p, pinned: nextPinned } : p))
        )
      }
    } catch {
      // Non-blocking inline action.
    } finally {
      setBusyPageAction((prev) => ({ ...prev, [key]: false }))
    }
  }

  const handleConfirm = async () => {
    if (submitting) return
    if (!agentName && !taskId) {
      setError('Missing agent or task target for handoff.')
      return
    }
    setSubmitting(true)
    setError(null)
    const note = transitionNote.trim()
    const selectedList = contextPages
      .filter((p) => selectedPages.has(p.name))
      .map((p) => p.name)

    let composedNote = note
    if (selectedList.length > 0) {
      const pointerBlock = [
        'Pinned ostk context pages (load via ostk context-load <name>):',
        ...selectedList.map((name) => `- ostk context-load ${name}`),
      ].join('\n')
      composedNote = note ? `${note}\n\n${pointerBlock}` : pointerBlock
    }

    try {
      let res: unknown
      if (agentName) {
        const payload: Record<string, unknown> = {
          target_runtime: targetRuntime,
          target_model: targetModel,
          task_id: taskId,
          transition_note: composedNote,
        }
        if (selectedList.length > 0) {
          payload.context_pages = selectedList
        }
        res = await api.post(
          `/agents/${encodeURIComponent(agentName)}/handoff-runtime`,
          payload
        )
      } else {
        const payload: Record<string, unknown> = {
          target_runtime: targetRuntime,
          target_model: targetModel,
          transition_note: composedNote,
        }
        if (selectedList.length > 0) {
          payload.context_pages = selectedList
        }
        res = await api.post(
          `/tasks/${encodeURIComponent(taskId!)}/handoff-runtime`,
          payload
        )
      }
      onHandedOff?.(res)
      onClose()
    } catch (err) {
      setError(
        err instanceof Error && err.message ? err.message : 'Could not hand off runtime.'
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="handoff-runtime-modal-title"
      data-testid="handoff-runtime-modal"
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl w-full max-w-md p-6 shadow-2xl space-y-4"
        style={{
          borderColor: 'var(--color-border, var(--theme-border))',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="handoff-runtime-modal-title"
              className="text-slate-900 dark:text-white font-semibold text-base"
            >
              Hand Off Runtime
            </h2>
            <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">
              {agentName
                ? `Transfer ${agentName} to another runtime with full context.`
                : taskId
                  ? `Continue needle ${taskId} on another runtime.`
                  : 'Switch runtime and model tier.'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close handoff modal"
            className="text-slate-500 hover:text-slate-800 dark:hover:text-white text-sm transition-colors disabled:opacity-50"
          >
            ✕
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label
              htmlFor="handoff-runtime-select"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Target Runtime
            </label>
            <select
              id="handoff-runtime-select"
              data-testid="handoff-runtime-select"
              value={targetRuntime}
              onChange={(e) => handleRuntimeChange(e.target.value as RuntimeKind)}
              disabled={submitting}
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-[var(--color-accent)]"
            >
              <option value="claude">Claude</option>
              <option value="gemini">Gemini</option>
            </select>
          </div>

          <div>
            <label
              htmlFor="handoff-model-select"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Model Tier
            </label>
            <select
              id="handoff-model-select"
              data-testid="handoff-model-select"
              value={targetModel}
              onChange={(e) => setTargetModel(e.target.value)}
              disabled={submitting}
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-[var(--color-accent)]"
            >
              {modelOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {contextPages.length > 0 && (
            <div
              data-testid="handoff-context-pages-section"
              className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 space-y-2"
              style={{
                backgroundColor: 'var(--color-surface, var(--theme-surface))',
                borderColor: 'var(--color-border, var(--theme-border))',
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-slate-700 dark:text-slate-300">
                  Context Pages (hot / warm / cold)
                </span>
                <span className="text-[11px] text-slate-500 dark:text-slate-400">
                  {selectedPages.size} selected
                </span>
              </div>

              <div className="max-h-40 overflow-y-auto space-y-1.5 pr-0.5">
                {contextPages.map((page) => {
                  const checked = selectedPages.has(page.name)
                  const restoreBusy = Boolean(busyPageAction[`restore:${page.name}`])
                  const pinBusy = Boolean(busyPageAction[`pin:${page.name}`])
                  return (
                    <div
                      key={page.name}
                      className="flex items-center justify-between gap-2 rounded-md border border-slate-200/80 dark:border-slate-700/80 bg-white/70 dark:bg-slate-900/60 px-2.5 py-1.5 text-xs"
                    >
                      <label className="flex items-center gap-2 min-w-0 flex-1 cursor-pointer">
                        <input
                          type="checkbox"
                          data-testid={`context-page-checkbox-${page.name}`}
                          checked={checked}
                          disabled={submitting}
                          onChange={() => handleTogglePage(page.name)}
                          style={{ accentColor: 'var(--color-accent)' }}
                          className="rounded border-slate-300 dark:border-slate-600"
                        />
                        <span
                          className="font-mono text-xs text-slate-800 dark:text-slate-200 truncate"
                          title={page.name}
                        >
                          {page.name}
                        </span>
                      </label>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <span
                          data-testid={`context-page-tier-${page.name}`}
                          className={`px-1.5 py-0.5 rounded border text-[10px] font-medium uppercase tracking-wide ${getTierBadgeClasses(page.tier)}`}
                        >
                          {page.tier}
                        </span>
                        <span className="text-[11px] text-slate-500 dark:text-slate-400 tabular-nums">
                          {page.tokens.toLocaleString()} tok
                        </span>
                        {page.tier !== 'hot' && (
                          <button
                            type="button"
                            data-testid={`context-page-restore-${page.name}`}
                            disabled={submitting || restoreBusy}
                            onClick={() => handleRestorePage(page)}
                            className="px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-[10px] font-medium text-slate-700 dark:text-slate-200 hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
                          >
                            Restore to hot
                          </button>
                        )}
                        <button
                          type="button"
                          data-testid={`context-page-pin-${page.name}`}
                          disabled={submitting || pinBusy}
                          onClick={() => handlePinPage(page)}
                          className={`px-1.5 py-0.5 rounded border text-[10px] font-medium transition-colors disabled:opacity-50 ${
                            page.pinned
                              ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
                              : 'border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:border-[var(--color-accent)]'
                          }`}
                        >
                          {page.pinned ? 'Pinned' : 'Pin'}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div>
            <label
              htmlFor="handoff-transition-note"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Transition Note (optional)
            </label>
            <textarea
              id="handoff-transition-note"
              data-testid="handoff-transition-note"
              rows={2}
              value={transitionNote}
              onChange={(e) => setTransitionNote(e.target.value)}
              disabled={submitting}
              placeholder="Explain why the handoff is happening (for example: rate limit or retry strategy)"
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-[var(--color-accent)] resize-none"
            />
          </div>
        </div>

        {error && (
          <div
            role="alert"
            data-testid="handoff-error"
            className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600 dark:text-rose-400"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            data-testid="cancel-handoff-btn"
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 text-sm rounded-lg text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="confirm-handoff-btn"
            onClick={handleConfirm}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium rounded-lg accent-bg hover:opacity-90 !text-white transition-colors disabled:opacity-50"
          >
            {submitting ? 'Handing off...' : 'Confirm Handoff'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default HandoffRuntimeModal
