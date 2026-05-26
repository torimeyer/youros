import { useNotificationStore } from '../stores/notifications'
import type { AppNotification } from '../stores/notifications'
import Icon from './Icon'

function statusIcon(status: string): { icon: string; color: string } {
  switch (status) {
    case 'completed':
      return { icon: 'check_circle', color: 'text-green-600 dark:text-green-400' }
    case 'failed':
      return { icon: 'error', color: 'text-red-600 dark:text-red-400' }
    case 'killed':
    case 'stopped':
      return { icon: 'cancel', color: 'text-orange-600 dark:text-orange-400' }
    case 'running':
    case 'spawned':
      return { icon: 'play_circle', color: 'text-blue-600 dark:text-blue-400' }
    default:
      return { icon: 'info', color: 'text-slate-600 dark:text-slate-400' }
  }
}

function statusMessage(status: string): string {
  switch (status) {
    case 'completed':
      return 'finished'
    case 'failed':
      return 'failed'
    case 'killed':
      return 'was cancelled'
    case 'stopped':
      return 'stopped'
    case 'running':
    case 'spawned':
      return 'started'
    default:
      return status
  }
}

// Returns true for statuses whose ``statusMessage`` is a real English
// verb (finished / failed / was cancelled / stopped / started). Used by
// the toast body to decide whether to fall back to the generic
// "Agent <verb>" message, or show a neutral "New notification" so a
// persistent notification type like ``roadmap_ready`` never leaks its
// raw identifier into the toast.
function isKnownAgentStatus(status: string): boolean {
  return (
    status === 'completed' ||
    status === 'failed' ||
    status === 'killed' ||
    status === 'stopped' ||
    status === 'running' ||
    status === 'spawned'
  )
}

// The backend emits persistent agent-completion rows with a title
// shaped like ``Agent done: <name>`` and a body set to the agent's
// description. The raw title, when truncated by a narrow toast, turns
// into ``Agent done: diagnose-me...``, and when the agent's name itself
// contains a word like "failure" the body then reads as a status
// report of failure. Split the prefix off here so the toast can render
// a neutral label ("Finished") alongside the full name on its own line.
const AGENT_DONE_PREFIX = 'Agent done: '

function splitAgentDoneTitle(raw: string): {
  statusLabel: string | null
  name: string
} {
  if (raw.startsWith(AGENT_DONE_PREFIX)) {
    return {
      statusLabel: 'Finished',
      name: raw.slice(AGENT_DONE_PREFIX.length),
    }
  }
  return { statusLabel: null, name: raw }
}

function Toast({
  notification,
  onDismiss,
}: {
  notification: AppNotification
  onDismiss: () => void
}) {
  const { icon, color } = statusIcon(notification.status)
  const { statusLabel, name } = splitAgentDoneTitle(notification.agentName)
  // Persistent notifications (roadmap_ready, upgrade, sync, etc.) come
  // through ``addPersistentToast`` and carry a human-readable ``body``
  // from the backend. Use that verbatim so the toast reads like
  // "Open roadmap.md. Type 'create tasks' in chat to break it down."
  // instead of the raw type string. Agent status-change toasts leave
  // ``body`` undefined and fall back to the generic message. Fail-safe:
  // if a persistent notification somehow arrives with no body and its
  // status does not map to a known agent status message, show a plain
  // "New notification" instead of surfacing "Agent roadmap_ready".
  const bodyText = notification.body
    ? notification.body
    : isKnownAgentStatus(notification.status)
      ? `Agent ${statusMessage(notification.status)}`
      : 'New notification'
  // For agent-completion toasts the full name lives on its own line and
  // is allowed to wrap to two lines so names like
  // "Diagnose memory consistency failure" never get cut to
  // "Agent done: diagnose-me..." which reads as a failure report.
  // The body (agent description) is suppressed because it typically
  // just repeats the name in the completion case.
  return (
    <div className="flex items-start gap-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 shadow-xl w-72 animate-toast-in">
      <Icon name={icon} size={20} className={`${color} mt-0.5 shrink-0`} />
      <div className="flex-1 min-w-0">
        {statusLabel ? (
          <>
            <p className="text-sm font-medium text-white">{statusLabel}</p>
            <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed break-words line-clamp-2">
              {name}
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-white truncate">{name}</p>
            <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{bodyText}</p>
          </>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="text-slate-600 hover:text-slate-600 dark:hover:text-slate-400 shrink-0 transition-colors"
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  )
}

export default function NotificationToasts() {
  const notifications = useNotificationStore((s) => s.notifications)
  const toastIds = useNotificationStore((s) => s.toastIds)
  const dismissToast = useNotificationStore((s) => s.dismissToast)

  const toasts = notifications.filter((n) => toastIds.includes(n.id))

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((n) => (
        <div key={n.id} className="pointer-events-auto">
          <Toast notification={n} onDismiss={() => dismissToast(n.id)} />
        </div>
      ))}
    </div>
  )
}
