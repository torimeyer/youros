import { useNotificationStore } from '../stores/notifications'
import type { AppNotification } from '../stores/notifications'
import Icon from './Icon'

function statusIcon(status: string): { icon: string; color: string } {
  switch (status) {
    case 'completed':
      return { icon: 'check_circle', color: 'text-green-400' }
    case 'failed':
      return { icon: 'error', color: 'text-red-400' }
    case 'killed':
    case 'stopped':
      return { icon: 'cancel', color: 'text-orange-400' }
    case 'running':
    case 'spawned':
      return { icon: 'play_circle', color: 'text-blue-400' }
    default:
      return { icon: 'info', color: 'text-slate-400' }
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

function Toast({
  notification,
  onDismiss,
}: {
  notification: AppNotification
  onDismiss: () => void
}) {
  const { icon, color } = statusIcon(notification.status)
  return (
    <div className="flex items-start gap-3 bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 shadow-xl w-72 animate-toast-in">
      <Icon name={icon} size={20} className={`${color} mt-0.5 shrink-0`} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-white truncate">{notification.agentName}</p>
        <p className="text-xs text-slate-400">Agent {statusMessage(notification.status)}</p>
      </div>
      <button
        onClick={onDismiss}
        className="text-slate-600 hover:text-slate-400 shrink-0 transition-colors"
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
