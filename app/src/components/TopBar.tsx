import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'
import type { AppNotification } from '../stores/notifications'
import { api } from '../lib/api'
import { isPushSupported, isSubscribed, subscribe as pushSubscribe, unsubscribe as pushUnsubscribe } from '../lib/pushNotifications'

interface PersistentNotification {
  id: string
  type: string
  title: string
  body: string
  action_label: string | null
  action_url: string | null
  read: boolean
  created_at: string
  metadata: Record<string, unknown>
}

interface TopBarProps {
  title: string
}

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
    case 'completed': return 'finished'
    case 'failed': return 'failed'
    case 'killed': return 'was cancelled'
    case 'stopped': return 'stopped'
    case 'running':
    case 'spawned': return 'started'
    default: return status
  }
}

function formatTimeAgo(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function NotificationItem({ n }: { n: AppNotification }) {
  const { icon, color } = statusIcon(n.status)
  return (
    <div className={`flex items-start gap-3 px-4 py-3 hover:bg-slate-800/50 transition-colors ${n.read ? 'opacity-60' : ''}`}>
      <Icon name={icon} size={18} className={`${color} mt-0.5 shrink-0`} />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white font-medium truncate">{n.agentName}</p>
        <p className="text-xs text-slate-400">Agent {statusMessage(n.status)}</p>
      </div>
      <span className="text-[10px] text-slate-600 shrink-0 mt-0.5">{formatTimeAgo(n.timestamp)}</span>
    </div>
  )
}

function PersistentNotificationItem({
  n,
  onRead,
}: {
  n: PersistentNotification
  onRead: (id: string) => void
}) {
  const navigate = useNavigate()
  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 hover:bg-slate-800/50 transition-colors cursor-pointer ${n.read ? 'opacity-60' : ''}`}
      onClick={() => {
        if (!n.read) onRead(n.id)
        if (n.action_url) navigate(n.action_url)
      }}
    >
      <Icon
        name={
          n.type === 'upgrade' ? 'system_update_alt'
          : n.type === 'agent' ? 'check_circle'
          : n.type === 'sync' ? 'sync'
          : n.type === 'task_overdue' ? 'schedule'
          : n.type === 'idea' ? 'lightbulb'
          : 'info'
        }
        size={18}
        className={
          (n.type === 'upgrade' ? 'text-blue-400'
          : n.type === 'agent' ? 'text-green-400'
          : n.type === 'sync' ? 'text-purple-400'
          : n.type === 'task_overdue' ? 'text-orange-400'
          : n.type === 'idea' ? 'text-yellow-400'
          : 'text-slate-400') + ' mt-0.5 shrink-0'
        }
      />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white font-medium">{n.title}</p>
        <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">{n.body}</p>
        {n.action_label && (
          <p className="text-xs text-blue-400 mt-1">{n.action_label} &rarr;</p>
        )}
      </div>
      <span className="text-[10px] text-slate-600 shrink-0 mt-0.5">
        {formatTimeAgo(n.created_at)}
      </span>
    </div>
  )
}

export default function TopBar({ title }: TopBarProps) {
  const navigate = useNavigate()
  const toggleChat = useAppStore((s) => s.toggleChat)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const displayOsName = useAppStore((s) => s.displayOsName())
  const instanceMode = useAppStore((s) => s.instanceMode)
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)
  const chatOpen = useAppStore((s) => s.chatOpen)
  const chatWidth = useAppStore((s) => s.chatWidth)
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 1024 : true
  )
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)')
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])
  const [showNotifications, setShowNotifications] = useState(false)
  const [, setTick] = useState(0)
  const [persistentNotifs, setPersistentNotifs] = useState<PersistentNotification[]>([])
  const [pushEnabled, setPushEnabled] = useState(false)
  const [pushToggling, setPushToggling] = useState(false)
  const [isOffline, setIsOffline] = useState(!navigator.onLine)

  const notifications = useNotificationStore((s) => s.notifications)
  const markAllRead = useNotificationStore((s) => s.markAllRead)
  const clearAll = useNotificationStore((s) => s.clearAll)

  // Single source of truth. The badge count and the dropdown body must read
  // from the exact same arrays, otherwise the bell can show "9+" while the
  // dropdown shows "You're all caught up". The unread counts below are
  // memoized derivations of the same lists the dropdown renders.
  const agentUnreadCount = useMemo(
    () => notifications.filter((n) => !n.read).length,
    [notifications]
  )
  const persistentUnread = useMemo(
    () => persistentNotifs.filter((n) => !n.read).length,
    [persistentNotifs]
  )
  const unreadCount = agentUnreadCount + persistentUnread

  const fetchPersistentNotifs = useCallback(async () => {
    try {
      const data = await api.get<PersistentNotification[]>('/notifications')
      setPersistentNotifs(Array.isArray(data) ? data : [])
    } catch {
      // ignore
    }
  }, [])

  // Re-render every minute so "X min ago" stays fresh
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(interval)
  }, [])

  // Proactively fetch the persistent notifications list on mount and poll
  // every 60 seconds. The badge count is derived from this list, so the
  // badge and the dropdown can never disagree.
  useEffect(() => {
    fetchPersistentNotifs()
    const interval = setInterval(fetchPersistentNotifs, 60_000)
    return () => clearInterval(interval)
  }, [fetchPersistentNotifs])

  // Check push subscription state on mount
  useEffect(() => {
    if (isPushSupported()) {
      isSubscribed().then(setPushEnabled).catch(() => {})
    }
  }, [])

  // Track online/offline state
  useEffect(() => {
    const goOnline = () => setIsOffline(false)
    const goOffline = () => setIsOffline(true)
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
    }
  }, [])

  const handleTogglePush = useCallback(async () => {
    setPushToggling(true)
    try {
      if (pushEnabled) {
        await pushUnsubscribe()
        setPushEnabled(false)
      } else {
        const ok = await pushSubscribe()
        setPushEnabled(ok)
      }
    } catch {
      // ignore
    } finally {
      setPushToggling(false)
    }
  }, [pushEnabled])

  const handleMarkPersistentRead = useCallback(async (id: string) => {
    try {
      await api.post(`/notifications/${id}/read`)
      setPersistentNotifs((prev) => prev.map((n) => n.id === id ? { ...n, read: true } : n))
    } catch {
      // ignore
    }
  }, [])

  const handleMarkAllPersistentRead = useCallback(async () => {
    try {
      await api.post('/notifications/read-all')
      setPersistentNotifs((prev) => prev.map((n) => ({ ...n, read: true })))
    } catch {
      // ignore
    }
  }, [])

  const handleOpenNotifications = async () => {
    // Just open the drawer. We intentionally do NOT mark everything as read
    // on open. Users need to see which notifications are new. Individual
    // items get marked read when clicked, and anything still unread gets
    // marked read when the drawer closes. Refresh the list so the dropdown
    // and badge are both up to date with the server.
    setShowNotifications(true)
    await fetchPersistentNotifs()
  }

  const handleCloseNotifications = useCallback(() => {
    setShowNotifications(false)
    // On close, flush any remaining unread items so the bell badge clears.
    // Clicking a specific item already marks that one read, so this only
    // catches the ones the user glanced at but did not click.
    if (agentUnreadCount > 0) markAllRead()
    if (persistentUnread > 0) {
      handleMarkAllPersistentRead()
    }
  }, [agentUnreadCount, markAllRead, persistentUnread, handleMarkAllPersistentRead])

  return (
    <header
      className="fixed top-0 left-0 lg:left-56 h-14 sm:h-16 bg-slate-950/80 backdrop-blur-md border-b border-slate-800/50 flex items-center justify-between px-4 sm:px-8 z-40 transition-[right] duration-200"
      style={{ right: chatOpen && isDesktop ? chatWidth : 0 }}
    >
      <div className="flex items-center gap-4 pl-10 lg:pl-0">
        <span className="font-bold text-slate-100 tracking-tight text-sm sm:text-base">{title}</span>
      </div>

      <div data-tour="search" className="flex-1 max-w-md mx-2 sm:mx-8 hidden sm:block">
        <button
          onClick={() => setCommandPaletteOpen(true)}
          className="w-full relative group flex items-center bg-slate-900 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-500 hover:text-slate-400 transition-all cursor-pointer border border-transparent hover:border-slate-700"
        >
          <Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-hover:text-blue-500" />
          <span>Search {displayOsName}</span>
          <kbd className="ml-auto text-[10px] font-mono text-slate-600 bg-slate-800 rounded px-1.5 py-0.5 border border-slate-700">
            ⌘K
          </kbd>
        </button>
      </div>

      <div className="flex items-center gap-2 sm:gap-4">
        <button
          onClick={() => {
            navigate('/tasks')
            setTimeout(() => window.dispatchEvent(new CustomEvent('myos-quick-add-task')), 100)
          }}
          className="p-2.5 sm:p-2 text-slate-400 hover:text-blue-400 transition-all"
          title="Add Task"
        >
          <Icon name="add_task" />
        </button>
        <button
          onClick={toggleChat}
          className="p-2.5 sm:p-2 text-slate-400 hover:text-blue-400 transition-all"
          title="Toggle Chat (⌘L)"
        >
          <Icon name="chat" />
        </button>
        <div className="relative">
          <button
            onClick={handleOpenNotifications}
            className="relative p-2.5 sm:p-2 text-slate-400 hover:text-blue-400 transition-all"
          >
            <Icon name="notifications" />
            {unreadCount > 0 && (
              <span className="absolute top-1 right-1 min-w-[16px] h-4 bg-blue-500 text-white text-[9px] font-bold rounded-full flex items-center justify-center px-1">
                {unreadCount > 9 ? '9+' : unreadCount}
              </span>
            )}
          </button>
          {showNotifications && (
            <>
              <div className="fixed inset-0 z-40" onClick={handleCloseNotifications} />
              <div className="absolute right-0 top-full mt-2 w-80 bg-slate-900 border border-slate-800 rounded-xl shadow-xl z-50 overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-sm font-semibold text-white">Notifications</span>
                  <div className="flex items-center gap-3">
                    {unreadCount > 0 && (
                      <button
                        onClick={() => {
                          if (agentUnreadCount > 0) markAllRead()
                          if (persistentUnread > 0) handleMarkAllPersistentRead()
                        }}
                        className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                      >
                        Mark all read
                      </button>
                    )}
                    {(notifications.length > 0 || persistentNotifs.length > 0) && (
                      <button
                        onClick={clearAll}
                        className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                      >
                        Clear all
                      </button>
                    )}
                    <button onClick={handleCloseNotifications} className="text-slate-500 hover:text-white">
                      <Icon name="close" size={16} />
                    </button>
                  </div>
                </div>

                {notifications.length === 0 && persistentNotifs.length === 0 ? (
                  <div className="p-6 text-center">
                    <Icon name="notifications_none" size={32} className="text-slate-700 mb-2" />
                    <p className="text-sm text-slate-500">You&apos;re all caught up.</p>
                    <p className="text-xs text-slate-600 mt-1">Alerts from agents and updates will show up here.</p>
                  </div>
                ) : (
                  <div className="max-h-80 overflow-y-auto divide-y divide-slate-800/50">
                    {persistentNotifs.map((n) => (
                      <PersistentNotificationItem
                        key={n.id}
                        n={n}
                        onRead={handleMarkPersistentRead}
                      />
                    ))}
                    {notifications.map((n) => (
                      <NotificationItem key={n.id} n={n} />
                    ))}
                  </div>
                )}
                {isPushSupported() && (
                  <div className="px-4 py-3 border-t border-slate-800 flex items-center justify-between">
                    <span className="text-xs text-slate-400">Push notifications</span>
                    <button
                      onClick={handleTogglePush}
                      disabled={pushToggling}
                      className={`w-9 h-5 rounded-full relative transition-colors ${
                        pushEnabled ? 'accent-bg' : 'bg-slate-700'
                      } ${pushToggling ? 'opacity-50' : ''}`}
                    >
                      <span
                        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                          pushEnabled ? 'left-4' : 'left-0.5'
                        }`}
                      />
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
        {isOffline && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-900/50 border border-amber-700/50">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
            <span className="text-xs text-amber-300">Offline</span>
          </div>
        )}
        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold ${instanceMode === 'team' ? 'bg-gradient-to-br from-indigo-500 to-blue-600' : 'bg-gradient-to-br from-pink-500 to-purple-600'}`}>
          {instanceMode === 'team' && enterpriseUser?.email
            ? enterpriseUser.email.charAt(0).toUpperCase()
            : displayOsName.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
  )
}
