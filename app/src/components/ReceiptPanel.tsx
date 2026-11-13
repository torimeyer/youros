import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api'

export interface ReceiptCommit {
  sha: string
  short_sha: string
  subject: string
  timestamp: string
}

export interface ReceiptTestRun {
  command: string
  exit_code: number
  timestamp: string
}

export interface ReceiptData {
  verified: boolean
  commits: ReceiptCommit[]
  diff_stat: string
  changed_files: string[]
  trace_linked: boolean
  test_runs: ReceiptTestRun[]
  missing_reasons: string[]
  repo_root?: string
  worktree_path?: string | null
  branch_name?: string | null
  base_sha?: string | null
  agent_name?: string | null
}

export interface ReceiptPanelProps {
  taskId?: string
  agentName?: string
  receipt?: ReceiptData | null
  onActionComplete?: () => void
}

function normalizeReceiptPayload(raw: unknown): ReceiptData | null {
  if (!raw || typeof raw !== 'object') return null
  const candidate = raw as Partial<ReceiptData>
  if (typeof candidate.verified !== 'boolean') return null
  return {
    verified: candidate.verified,
    commits: Array.isArray(candidate.commits) ? candidate.commits : [],
    diff_stat: typeof candidate.diff_stat === 'string' ? candidate.diff_stat : '',
    changed_files: Array.isArray(candidate.changed_files) ? candidate.changed_files : [],
    trace_linked: Boolean(candidate.trace_linked),
    test_runs: Array.isArray(candidate.test_runs) ? candidate.test_runs : [],
    missing_reasons: Array.isArray(candidate.missing_reasons) ? candidate.missing_reasons : [],
    repo_root: candidate.repo_root,
    worktree_path: candidate.worktree_path ?? null,
    branch_name: candidate.branch_name ?? null,
    base_sha: candidate.base_sha ?? null,
    agent_name: candidate.agent_name ?? null,
  }
}

export function ReceiptPanel({
  taskId,
  agentName,
  receipt: preloadedReceipt,
  onActionComplete,
}: ReceiptPanelProps) {
  const [fetchedReceipt, setFetchedReceipt] = useState<ReceiptData | null>(null)
  const [busyAction, setBusyAction] = useState<'merge' | 'reset' | 'pin' | null>(null)
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null)
  const [pinKey, setPinKey] = useState('')
  const [pinValue, setPinValue] = useState('')

  const loadReceipt = useCallback(async () => {
    if (!taskId || preloadedReceipt != null) return
    const query = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : ''
    try {
      const res = await api.get<unknown>(`/tasks/${encodeURIComponent(taskId)}/receipt${query}`)
      setFetchedReceipt(normalizeReceiptPayload(res))
    } catch {
      setFetchedReceipt(null)
    }
  }, [taskId, agentName, preloadedReceipt])

  useEffect(() => {
    let cancelled = false
    if (preloadedReceipt != null) {
      setFetchedReceipt(null)
      return
    }
    if (!taskId) {
      setFetchedReceipt(null)
      return
    }
    const query = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : ''
    api
      .get<unknown>(`/tasks/${encodeURIComponent(taskId)}/receipt${query}`)
      .then((res) => {
        if (!cancelled) {
          setFetchedReceipt(normalizeReceiptPayload(res))
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFetchedReceipt(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [taskId, agentName, preloadedReceipt])

  const activeReceipt =
    preloadedReceipt != null ? normalizeReceiptPayload(preloadedReceipt) : fetchedReceipt

  if (!activeReceipt) {
    return <div data-testid="receipt-panel" className="hidden" />
  }

  const effectiveAgentName = agentName || activeReceipt.agent_name || undefined
  const latestTestRun =
    activeReceipt.test_runs.length > 0
      ? activeReceipt.test_runs[activeReceipt.test_runs.length - 1]
      : null
  const testsExitZero = latestTestRun !== null && latestTestRun.exit_code === 0

  const handleMergeAndReap = async () => {
    if (!effectiveAgentName || !activeReceipt.verified || busyAction) return
    setBusyAction('merge')
    setFeedback(null)
    try {
      await api.post(`/agents/${encodeURIComponent(effectiveAgentName)}/merge-and-reap`, {})
      setFeedback({ ok: true, text: 'Worktree merged and reaped.' })
      onActionComplete?.()
      await loadReceipt()
    } catch {
      setFeedback({ ok: false, text: 'Merge and reap failed.' })
    } finally {
      setBusyAction(null)
    }
  }

  const handleResetSavePoint = async () => {
    if (!effectiveAgentName || busyAction) return
    setBusyAction('reset')
    setFeedback(null)
    try {
      await api.post(`/agents/${encodeURIComponent(effectiveAgentName)}/reset-save-point`, {})
      setFeedback({ ok: true, text: 'Reset to save point complete.' })
      onActionComplete?.()
      await loadReceipt()
    } catch {
      setFeedback({ ok: false, text: 'Reset to save point failed.' })
    } finally {
      setBusyAction(null)
    }
  }

  const handlePinDecision = async () => {
    const trimmedKey = pinKey.trim()
    const trimmedValue = pinValue.trim()
    if (!effectiveAgentName || !trimmedKey || !trimmedValue || busyAction) return
    setBusyAction('pin')
    setFeedback(null)
    try {
      await api.post(`/agents/${encodeURIComponent(effectiveAgentName)}/pin-decision`, {
        key: trimmedKey,
        value: trimmedValue,
      })
      setPinKey('')
      setPinValue('')
      setFeedback({ ok: true, text: `Pinned decision: ${trimmedKey}` })
      onActionComplete?.()
    } catch {
      setFeedback({ ok: false, text: 'Could not pin decision.' })
    } finally {
      setBusyAction(null)
    }
  }

  return (
    <div
      data-testid="receipt-panel"
      className="mt-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/60 p-3 text-xs text-slate-700 dark:text-slate-300 space-y-2.5"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header + status pill + verification badges */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-400">
          Cryptographic Receipt
        </span>
        {activeReceipt.verified ? (
          <span
            data-testid="receipt-status-verified"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/30"
          >
            Verified
          </span>
        ) : (
          <span
            data-testid="receipt-status-unverified"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-semibold bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/30"
          >
            Missing Proof
          </span>
        )}

        <span
          data-testid="receipt-trace-badge"
          className={`inline-flex items-center px-2 py-0.5 rounded-full font-medium border ${
            activeReceipt.trace_linked
              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
              : 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30'
          }`}
        >
          {activeReceipt.trace_linked ? 'ostk trace linked' : 'ostk trace missing'}
        </span>

        <span
          data-testid="receipt-test-badge"
          className={`inline-flex items-center px-2 py-0.5 rounded-full font-medium border ${
            testsExitZero
              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
              : 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30'
          }`}
        >
          {testsExitZero
            ? 'Tests exit 0'
            : latestTestRun
              ? `Tests exit ${latestTestRun.exit_code}`
              : 'No audit test run'}
        </span>
      </div>

      {/* Commits list */}
      {activeReceipt.commits.length > 0 && (
        <ul data-testid="receipt-commits-list" className="space-y-1">
          {activeReceipt.commits.map((c) => (
            <li key={c.sha} className="flex items-center gap-2">
              <code className="font-mono px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-emerald-700 dark:text-emerald-300">
                {c.short_sha || c.sha.slice(0, 8)}
              </code>
              <span className="truncate">{c.subject}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Diff stat */}
      {activeReceipt.diff_stat && (
        <pre
          data-testid="receipt-diff-stat"
          className="font-mono text-[11px] bg-slate-100 dark:bg-slate-950/70 border border-slate-200 dark:border-slate-800 rounded p-2 overflow-x-auto whitespace-pre-wrap"
        >
          {activeReceipt.diff_stat}
        </pre>
      )}

      {/* Missing reasons bullet list when unverified */}
      {!activeReceipt.verified && activeReceipt.missing_reasons.length > 0 && (
        <ul
          data-testid="receipt-missing-reasons"
          className="list-disc list-inside space-y-0.5 text-rose-700 dark:text-rose-300"
        >
          {activeReceipt.missing_reasons.map((reason, idx) => (
            <li key={idx}>{reason}</li>
          ))}
        </ul>
      )}

      {/* Agent worktree actions */}
      {effectiveAgentName && (
        <div className="pt-2 border-t border-slate-200 dark:border-slate-800 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="receipt-merge-reap-btn"
              disabled={!activeReceipt.verified || busyAction !== null}
              onClick={handleMergeAndReap}
              className="px-2.5 py-1 rounded-md font-medium bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Merge and Reap Worktree
            </button>
            <button
              type="button"
              data-testid="receipt-reset-save-point-btn"
              disabled={busyAction !== null}
              onClick={handleResetSavePoint}
              className="px-2.5 py-1 rounded-md font-medium border border-amber-500/40 bg-amber-500/15 hover:bg-amber-500/25 text-amber-800 dark:text-amber-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Reset to Save Point
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <input
              type="text"
              data-testid="pin-decision-key-input"
              value={pinKey}
              onChange={(e) => setPinKey(e.target.value)}
              placeholder="Decision key"
              className="bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <input
              type="text"
              data-testid="pin-decision-value-input"
              value={pinValue}
              onChange={(e) => setPinValue(e.target.value)}
              placeholder="Decision value"
              className="flex-1 min-w-[140px] bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button
              type="button"
              data-testid="pin-decision-submit-btn"
              disabled={busyAction !== null || !pinKey.trim() || !pinValue.trim()}
              onClick={handlePinDecision}
              className="px-2.5 py-1 rounded-md font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Pin Decision
            </button>
          </div>
        </div>
      )}

      {feedback && (
        <p
          data-testid="receipt-action-feedback"
          className={feedback.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}
        >
          {feedback.text}
        </p>
      )}
    </div>
  )
}

export default ReceiptPanel
