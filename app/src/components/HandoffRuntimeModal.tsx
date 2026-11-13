import React, { useState, useEffect } from 'react'
import { api } from '../lib/api'

export type RuntimeKind = 'claude' | 'gemini'

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

  useEffect(() => {
    if (!isOpen) return
    const next = inferOppositeTarget(currentModel)
    setTargetRuntime(next.runtime)
    setTargetModel(next.model)
    setTransitionNote('')
    setError(null)
    setSubmitting(false)
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

  const handleConfirm = async () => {
    if (submitting) return
    if (!agentName && !taskId) {
      setError('Missing agent or task target for handoff.')
      return
    }
    setSubmitting(true)
    setError(null)
    const note = transitionNote.trim()
    try {
      let res: unknown
      if (agentName) {
        res = await api.post(`/agents/${encodeURIComponent(agentName)}/handoff-runtime`, {
          target_runtime: targetRuntime,
          target_model: targetModel,
          task_id: taskId,
          transition_note: note,
        })
      } else {
        res = await api.post(`/tasks/${encodeURIComponent(taskId!)}/handoff-runtime`, {
          target_runtime: targetRuntime,
          target_model: targetModel,
          transition_note: note,
        })
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
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
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
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
            >
              {modelOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

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
              className="w-full rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
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
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-50"
          >
            {submitting ? 'Handing off...' : 'Confirm Handoff'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default HandoffRuntimeModal
