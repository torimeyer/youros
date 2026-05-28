import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'

// Shape of a single review row returned by the backend. Fields mirror
// services/task_audit.py so no ad-hoc translation layer is needed.
interface ReviewEntry {
  task_id: string
  title?: string
  reason_guess: string
  reason_label: string
  evidence: string
  confidence?: 'low' | 'medium'
  duplicate_of?: string | null
}

interface ClosedEntry {
  task_id: string
  reason: string
  evidence: string
}

interface AuditResults {
  closed: ClosedEntry[]
  review: ReviewEntry[]
  skipped_irl: number
  errors: Array<{ task_id: string; message: string }>
}

interface AuditStatus {
  status: 'running' | 'done' | 'failed'
  checked: number
  total: number
  results: AuditResults
}

interface StartResponse {
  job_id: string
}

interface Props {
  open: boolean
  onClose: () => void
  onTasksChanged?: () => void
}

// Convert a reason_guess bucket into the plain language summary line we
// show at the top of the modal after the job finishes. Never uses the
// literal verdict word (no "completed:" prefix) so the copy matches the
// CLAUDE.md writing-style rules.
function buildSummary(results: AuditResults): string {
  // "closed" here refers to tasks the user has already approved and
  // confirmed during this modal session. The other three counts pull
  // from what the classifier wants to propose in review.
  const proposedCompleted = results.review.filter(
    (r) => r.reason_guess === 'completed',
  ).length
  const proposedDuplicate = results.review.filter(
    (r) => r.reason_guess === 'duplicate',
  ).length
  const proposedArchived = results.review.filter(
    (r) => r.reason_guess === 'archived',
  ).length
  const actualClosed = results.closed.length

  const parts: string[] = []
  if (actualClosed > 0) {
    parts.push(
      `${actualClosed} closed in this session`,
    )
  }
  parts.push(
    `Proposed: ${proposedCompleted} done, ${proposedDuplicate} duplicate, ${proposedArchived} no longer relevant.`,
  )
  parts.push(
    `Skipped ${results.skipped_irl} real-life needle${results.skipped_irl === 1 ? '' : 's'}.`,
  )
  return parts.join(' ')
}

export default function TasksAuditModal({ open, onClose, onTasksChanged }: Props) {
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<AuditStatus | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  // Tracks whether this modal instance has already kicked off the POST.
  // We need it so the open/close toggle does not race with itself.
  const startedRef = useRef(false)

  const pollStatus = useCallback(async (id: string) => {
    try {
      const next = await api.get<AuditStatus>(`/tasks/audit/${id}`)
      setStatus(next)
      return next
    } catch (err) {
      reportError('Audit status poll failed', err)
      return null
    }
  }, [])

  // Start the job when the modal opens for the first time in a session.
  useEffect(() => {
    if (!open) {
      startedRef.current = false
      setJobId(null)
      setStatus(null)
      setStartError(null)
      return
    }
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const res = await api.post<StartResponse>('/tasks/audit')
        setJobId(res.job_id)
        // Immediately fetch initial status so the modal does not show a
        // blank "Checked 0 of 0" flash.
        await pollStatus(res.job_id)
      } catch (err) {
        reportError('Audit start failed', err)
        setStartError('Could not start the audit. Please try again.')
      }
    })()
  }, [open, pollStatus])

  // Poll every 500ms until the job is done or failed.
  useEffect(() => {
    if (!jobId) return
    if (status && status.status !== 'running') return
    const timer = setInterval(() => {
      pollStatus(jobId)
    }, 500)
    return () => clearInterval(timer)
  }, [jobId, status, pollStatus])

  const handleClose = useCallback(() => {
    onClose()
    if (onTasksChanged) onTasksChanged()
  }, [onClose, onTasksChanged])

  // Escape closes the modal.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, handleClose])

  const handleApprove = async (entry: ReviewEntry) => {
    if (!jobId) return
    const reason =
      entry.reason_guess === 'completed' ||
      entry.reason_guess === 'duplicate' ||
      entry.reason_guess === 'archived'
        ? entry.reason_guess
        : 'completed'
    setApprovingId(entry.task_id)
    try {
      await api.post(`/tasks/audit/${jobId}/approve`, {
        task_id: entry.task_id,
        reason,
      })
      await pollStatus(jobId)
    } catch (err) {
      reportError('Approve failed', err)
    } finally {
      setApprovingId(null)
    }
  }

  const handleReject = async (entry: ReviewEntry) => {
    if (!jobId) return
    setApprovingId(entry.task_id)
    try {
      await api.post(`/tasks/audit/${jobId}/reject`, {
        task_id: entry.task_id,
      })
      await pollStatus(jobId)
    } catch (err) {
      reportError('Reject failed', err)
    } finally {
      setApprovingId(null)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="tasks-audit-modal"
      role="dialog"
      aria-label="Needles audit"
    >
      <div className="w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-slate-900 border border-slate-800 rounded-xl shadow-2xl">
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-start gap-2">
            <Icon name="fact_check" className="text-lg text-blue-400 mt-0.5" />
            <div>
              <h2
                className="text-base font-medium text-slate-100"
                data-testid="audit-review-headline"
              >
                Review every needle. Nothing closes until you say so.
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                yourOS will suggest what it thinks is done, duplicate, or no
                longer relevant. You decide one by one.
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-1 text-slate-500 hover:text-slate-200 shrink-0"
            aria-label="Close"
          >
            <Icon name="close" className="text-lg" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {startError && (
            <p className="text-sm text-red-400">{startError}</p>
          )}

          {!status && !startError && (
            <p className="text-sm text-slate-400">
              Audit started. You will review every result.
            </p>
          )}

          {status && (
            <>
              {status.status === 'running' && (
                <p
                  className="text-sm text-slate-300"
                  data-testid="audit-progress"
                >
                  Checked {status.checked} of {status.total} needles...
                </p>
              )}

              {status.status === 'done' && (
                <p className="text-sm text-slate-300" data-testid="audit-summary">
                  {buildSummary(status.results)}
                </p>
              )}

              {status.status === 'failed' && (
                <p className="text-sm text-red-400">
                  The audit could not finish. Please try again.
                </p>
              )}

              <div
                className="space-y-2"
                data-testid="audit-review-list"
              >
                {status.results.review.length === 0 && status.status === 'done' && (
                  <p className="text-sm text-slate-500">
                    Nothing to review. Your needle list is clean.
                  </p>
                )}
                {status.results.review.map((entry) => (
                  <div
                    key={entry.task_id}
                    data-testid={`audit-review-row-${entry.task_id}`}
                    className="flex items-start gap-3 p-3 bg-slate-950/60 border border-slate-800 rounded-lg"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-slate-500 font-mono">
                          #{entry.task_id}
                        </span>
                        <span className="text-sm text-slate-200">
                          {entry.title || '(no title)'}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-400">
                        {entry.reason_label}
                      </p>
                      {entry.evidence && (
                        <p className="mt-0.5 text-xs text-slate-500 italic">
                          {entry.evidence}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleApprove(entry)}
                        disabled={approvingId === entry.task_id}
                        data-testid={`audit-close-${entry.task_id}`}
                        className="px-3 py-1 text-xs rounded-lg bg-green-500/20 hover:bg-green-500/30 text-green-300 border border-green-500/30 disabled:opacity-50"
                      >
                        Close this needle
                      </button>
                      <button
                        onClick={() => handleReject(entry)}
                        disabled={approvingId === entry.task_id}
                        data-testid={`audit-keep-${entry.task_id}`}
                        className="px-3 py-1 text-xs rounded-lg bg-slate-700/40 hover:bg-slate-700/60 text-slate-300 border border-slate-700 disabled:opacity-50"
                      >
                        Keep
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              {status.status === 'done' && (
                <div className="flex justify-end pt-2 border-t border-slate-800">
                  <button
                    onClick={handleClose}
                    className="px-4 py-1.5 text-sm rounded-lg bg-blue-500/20 hover:bg-blue-500/30 text-blue-300 border border-blue-500/30"
                  >
                    Done
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
