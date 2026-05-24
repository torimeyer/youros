import { useState, useCallback } from 'react'
import Icon from './Icon'
import { LoadingState } from './ui'
import { api } from '../lib/api'

interface TriagedMessage {
  id: string
  thread_id: string
  subject: string
  from_name: string
  from_email: string
  snippet: string
  date: string
  is_unread: boolean
  category: 'action_needed' | 'task_worthy' | 'informational' | 'noise'
  reason: string
  task_title?: string
  task_description?: string
}

interface TriageSummary {
  action_needed: number
  task_worthy: number
  informational: number
  noise: number
}

interface TriageResult {
  ok: boolean
  messages: TriagedMessage[]
  summary: TriageSummary
  triaged_at: string | null
}

type ActionState = 'idle' | 'loading' | 'done' | 'error'

const CATEGORY_CONFIG = {
  action_needed: {
    label: 'Needs reply',
    color: 'text-red-400',
    bg: 'bg-red-500/15 border-red-500/30',
    dot: 'bg-red-400',
    icon: 'reply' as const,
  },
  task_worthy: {
    label: 'Task found',
    color: 'text-amber-400',
    bg: 'bg-amber-500/15 border-amber-500/30',
    dot: 'bg-amber-400',
    icon: 'task_alt' as const,
  },
  informational: {
    label: 'FYI',
    color: 'text-blue-400',
    bg: 'bg-blue-500/10 border-blue-500/20',
    dot: 'bg-blue-400',
    icon: 'info' as const,
  },
  noise: {
    label: 'Noise',
    color: 'text-slate-500',
    bg: 'bg-slate-800/40 border-slate-700/30',
    dot: 'bg-slate-600',
    icon: 'volume_off' as const,
  },
}

function SummaryBadge({ count, label, color }: { count: number; label: string; color: string }) {
  if (count === 0) return null
  return (
    <span className={`text-sm font-medium ${color}`}>
      {count} {label}
    </span>
  )
}

export default function GmailTriage() {
  const [result, setResult] = useState<TriageResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [actionStates, setActionStates] = useState<Record<string, ActionState>>({})
  const [actionFeedback, setActionFeedback] = useState<Record<string, string>>({})
  const [draftedReplies, setDraftedReplies] = useState<Record<string, string>>({})
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const runTriage = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await api.post<TriageResult>('/gmail/triage', {})
      setResult(res)
      setDismissed(new Set())
      setActionStates({})
      setActionFeedback({})
      setDraftedReplies({})
      setExpandedId(null)
    } catch {
      setError('Triage could not run. Make sure Gmail is connected and try again.')
    } finally {
      setRunning(false)
    }
  }, [])

  const applyAction = useCallback(
    async (
      action: string,
      messageId: string,
      threadId?: string,
    ) => {
      setActionStates((prev) => ({ ...prev, [messageId + ':' + action]: 'loading' }))
      try {
        const res = await api.post<{ ok: boolean; draft?: string; title?: string }>(
          '/gmail/triage/apply',
          { action, message_id: messageId, thread_id: threadId },
        )
        setActionStates((prev) => ({ ...prev, [messageId + ':' + action]: 'done' }))
        if (action === 'draft_reply' && res.draft) {
          setDraftedReplies((prev) => ({ ...prev, [messageId]: res.draft! }))
        }
        if (action === 'create_task' && res.title) {
          setActionFeedback((prev) => ({
            ...prev,
            [messageId]: `Task created: ${res.title}`,
          }))
        }
        if (action === 'archive' || action === 'skip') {
          setDismissed((prev) => new Set(prev).add(messageId))
        }
      } catch {
        setActionStates((prev) => ({ ...prev, [messageId + ':' + action]: 'error' }))
      }
    },
    [],
  )

  const actionKey = (msgId: string, action: string) => msgId + ':' + action

  const visibleMessages = result
    ? result.messages.filter((m) => !dismissed.has(m.id))
    : []

  const summary = result?.summary

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Icon name="auto_awesome" size={18} className="text-violet-400" />
          <h2 className="text-base font-semibold">Smart Triage</h2>
          {summary && (
            <div className="flex items-center gap-3 ml-2">
              <SummaryBadge
                count={summary.action_needed}
                label={summary.action_needed === 1 ? 'needs reply' : 'need replies'}
                color="text-red-400"
              />
              <SummaryBadge
                count={summary.task_worthy}
                label={summary.task_worthy === 1 ? 'task found' : 'tasks found'}
                color="text-amber-400"
              />
              <SummaryBadge
                count={summary.noise}
                label="noise"
                color="text-slate-500"
              />
            </div>
          )}
        </div>
        <button
          onClick={runTriage}
          disabled={running}
          aria-label="Run email triage"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
        >
          <Icon
            name={running ? 'progress_activity' : 'auto_awesome'}
            size={16}
            className={running ? 'animate-spin' : ''}
          />
          {running ? 'Triaging...' : result ? 'Re-run triage' : 'Run triage'}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Loading */}
      {running && !result && (
        <LoadingState variant="spinner" message="Reading your inbox and classifying messages..." />
      )}

      {/* No results yet */}
      {!running && !result && !error && (
        <p className="text-sm text-slate-500 py-2">
          Run triage to automatically sort your unread email by what needs attention.
        </p>
      )}

      {/* Results */}
      {result && visibleMessages.length === 0 && (
        <p className="text-sm text-slate-400 py-2">
          {result.messages.length === 0
            ? 'No unread messages found.'
            : 'All messages handled.'}
        </p>
      )}

      {visibleMessages.length > 0 && (
        <div className="space-y-2">
          {visibleMessages.map((msg) => {
            const cfg = CATEGORY_CONFIG[msg.category]
            const isExpanded = expandedId === msg.id
            const draft = draftedReplies[msg.id]
            const feedback = actionFeedback[msg.id]

            return (
              <div
                key={msg.id}
                className={`border rounded-xl overflow-hidden ${cfg.bg}`}
              >
                {/* Message row */}
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : msg.id)}
                  aria-expanded={isExpanded}
                  className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-white/5 transition-colors"
                >
                  <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-white truncate">
                        {msg.from_name || msg.from_email}
                      </span>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full bg-slate-900/60 ${cfg.color}`}>
                        {cfg.label}
                      </span>
                    </div>
                    <p className="text-sm text-slate-300 truncate mt-0.5">{msg.subject}</p>
                    <p className="text-xs text-slate-500 truncate mt-0.5">{msg.snippet}</p>
                  </div>
                  <Icon
                    name={isExpanded ? 'expand_less' : 'expand_more'}
                    size={18}
                    className="text-slate-500 shrink-0 mt-1"
                  />
                </button>

                {/* Expanded detail + actions */}
                {isExpanded && (
                  <div className="px-4 pb-4 space-y-3 border-t border-white/10 pt-3">
                    {/* Reason */}
                    {msg.reason && (
                      <p className="text-xs text-slate-400 italic">{msg.reason}</p>
                    )}

                    {/* Task suggestion */}
                    {msg.category === 'task_worthy' && msg.task_title && (
                      <div className="bg-slate-900/60 rounded-lg px-3 py-2 text-sm">
                        <p className="text-slate-300 font-medium">{msg.task_title}</p>
                        {msg.task_description && (
                          <p className="text-slate-500 text-xs mt-0.5">{msg.task_description}</p>
                        )}
                      </div>
                    )}

                    {/* Draft */}
                    {draft && (
                      <div className="bg-slate-900/60 rounded-lg px-3 py-2 text-sm">
                        <p className="text-xs text-slate-500 mb-1">Draft reply:</p>
                        <p className="text-slate-300 whitespace-pre-wrap">{draft}</p>
                      </div>
                    )}

                    {/* Feedback */}
                    {feedback && (
                      <div className="flex items-center gap-2 text-sm text-emerald-400">
                        <Icon name="check_circle" size={14} />
                        {feedback}
                      </div>
                    )}

                    {/* Action buttons */}
                    <div className="flex items-center gap-2 flex-wrap">
                      {msg.category === 'task_worthy' && (
                        <ActionButton
                          label="Create Task"
                          icon="add_task"
                          state={actionStates[actionKey(msg.id, 'create_task')]}
                          doneLabel="Task created"
                          onClick={() => applyAction('create_task', msg.id)}
                          className="bg-amber-600 hover:bg-amber-700"
                        />
                      )}

                      {msg.category === 'action_needed' && !draft && (
                        <ActionButton
                          label="Draft Reply"
                          icon="auto_awesome"
                          state={actionStates[actionKey(msg.id, 'draft_reply')]}
                          doneLabel="Draft ready"
                          onClick={() => applyAction('draft_reply', msg.id, msg.thread_id)}
                          className="bg-blue-600 hover:bg-blue-700"
                        />
                      )}

                      <ActionButton
                        label="Archive"
                        icon="archive"
                        state={actionStates[actionKey(msg.id, 'archive')]}
                        doneLabel="Archived"
                        onClick={() => applyAction('archive', msg.id)}
                        className="bg-slate-700 hover:bg-slate-600"
                      />

                      <ActionButton
                        label="Skip"
                        icon="close"
                        state={actionStates[actionKey(msg.id, 'skip')]}
                        doneLabel="Skipped"
                        onClick={() => applyAction('skip', msg.id)}
                        className="bg-slate-800 hover:bg-slate-700 text-slate-400"
                      />
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {result?.triaged_at && (
        <p className="text-xs text-slate-600">
          Last triaged{' '}
          {new Date(result.triaged_at).toLocaleTimeString([], {
            hour: 'numeric',
            minute: '2-digit',
          })}
        </p>
      )}
    </div>
  )
}

function ActionButton({
  label,
  icon,
  state,
  doneLabel,
  onClick,
  className = '',
}: {
  label: string
  icon: string
  state: ActionState | undefined
  doneLabel: string
  onClick: () => void
  className?: string
}) {
  const isLoading = state === 'loading'
  const isDone = state === 'done'
  const isError = state === 'error'

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isLoading || isDone}
      aria-label={label}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-60 text-white ${className}`}
    >
      <Icon
        name={
          isLoading
            ? 'progress_activity'
            : isDone
            ? 'check_circle'
            : isError
            ? 'error'
            : icon
        }
        size={14}
        className={isLoading ? 'animate-spin' : ''}
      />
      {isLoading ? 'Working...' : isDone ? doneLabel : isError ? 'Failed' : label}
    </button>
  )
}
