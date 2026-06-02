import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { formatRelative } from '../lib/time'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'
import type { AppNotification } from '../stores/notifications'
import { useNotificationsStore } from '../stores/notificationsStore'
import type { Notification as WsNotification } from '../stores/notificationsStore'
import { api } from '../lib/api'
import { isPushSupported, isSubscribed, subscribe as pushSubscribe, unsubscribe as pushUnsubscribe } from '../lib/pushNotifications'

interface TopBarProps {
  title: string
}

const isMac = () => navigator.platform.toUpperCase().includes('MAC') || navigator.userAgent.toUpperCase().includes('MAC OS')
const modKey = isMac() ? '⌘' : 'Ctrl+'

// Persistent notifications whose type should pop a toast as soon as the
// TopBar poll discovers them. Anything outside this set still lands in
// the bell drawer but does not interrupt the user. Kept broad enough to
// cover the common demo flows (roadmap generation, agent finished,
// sync-status, task overdue, spec/feature completion) without toasting
// chatter. spec_complete is included because a Build-it landing is one
// of the loudest wins in the app and Tori must never miss it: without
// this row, the backend wrote "Your feature is live" to the drawer but
// no toast popped, making the landing feel silent.
const TOAST_WORTHY_PERSISTENT_TYPES = new Set<string>([
  'roadmap_ready',
  'agent',
  'sync',
  'task_overdue',
  'upgrade',
  'spec_complete',
])

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
    case 'completed': return 'finished'
    case 'failed': return 'failed'
    case 'killed': return 'was cancelled'
    case 'stopped': return 'stopped'
    case 'running':
    case 'spawned': return 'started'
    default: return status
  }
}



function NotificationItem({ n }: { n: AppNotification }) {
  const { icon, color } = statusIcon(n.status)
  return (
    <div className={`flex items-start gap-3 px-4 py-3 hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors ${n.read ? 'opacity-60' : ''}`}>
      <Icon name={icon} size={18} className={`${color} mt-0.5 shrink-0`} />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white font-medium truncate">{n.agentName}</p>
        <p className="text-xs text-slate-600 dark:text-slate-400">Agent {statusMessage(n.status)}</p>
      </div>
      <span className="text-[10px] text-slate-600 shrink-0 mt-0.5">{formatRelative(n.timestamp)}</span>
    </div>
  )
}

function PersistentNotificationItem({
  n,
  onRead,
}: {
  n: WsNotification
  onRead: (id: string) => void
}) {
  const navigate = useNavigate()
  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer ${n.read ? 'opacity-60' : ''}`}
      onClick={() => {
        if (!n.read) onRead(n.id)
        if (n.action_url) navigate(n.action_url as string)
      }}
    >
      <Icon
        name={
          n.type === 'upgrade' ? 'system_update_alt'
          : n.type === 'agent' ? 'check_circle'
          : n.type === 'sync' ? 'sync'
          : n.type === 'task_overdue' ? 'schedule'
          : 'info'
        }
        size={18}
        className={
          (n.type === 'upgrade' ? 'text-blue-600 dark:text-blue-400'
          : n.type === 'agent' ? 'text-green-600 dark:text-green-400'
          : n.type === 'sync' ? 'text-purple-600 dark:text-purple-400'
          : n.type === 'task_overdue' ? 'text-orange-600 dark:text-orange-400'
          : 'text-slate-600 dark:text-slate-400') + ' mt-0.5 shrink-0'
        }
      />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white font-medium">{n.title}</p>
        <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 leading-relaxed">{n.body ?? ''}</p>
        {n.action_label && (
          <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">{n.action_label} &rarr;</p>
        )}
      </div>
      <span className="text-[10px] text-slate-600 shrink-0 mt-0.5">
        {formatRelative(n.created_at)}
      </span>
    </div>
  )
}

export default function TopBar({ title }: TopBarProps) {
  const navigate = useNavigate()
  const toggleChat = useAppStore((s) => s.toggleChat)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const displayOsName = useAppStore((s) => s.displayOsName())
  const chatOpen = useAppStore((s) => s.chatOpen)
  const chatWidth = useAppStore((s) => s.chatWidth)
  const tourComplete = useAppStore((s) => s.tourComplete)
  const setShowTour = useAppStore((s) => s.setShowTour)
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
  const [pushEnabled, setPushEnabled] = useState(false)
  const [pushToggling, setPushToggling] = useState(false)
  const [isOffline, setIsOffline] = useState(!navigator.onLine)

  const notifications = useNotificationStore((s) => s.notifications)
  const markAllRead = useNotificationStore((s) => s.markAllRead)
  const addPersistentToast = useNotificationStore((s) => s.addPersistentToast)
  const clearAll = useNotificationStore((s) => s.clearAll)
  const wsNotifications = useNotificationsStore((s) => s.notifications)
  // True once the first WS snapshot (or first REST poll) has been
  // delivered. We gate seenNotifIdsRef seeding on this flag so we never
  // seed from the empty list that exists before the WS connects, which
  // would cause every notification in the real first snapshot to look
  // "new" and fire a stale toast (→1342).
  const snapshotReceived = useNotificationsStore((s) => s.snapshotReceived)

  // Single source of truth. The badge count and the dropdown body must read
  // from the exact same arrays, otherwise the bell can show "9+" while the
  // dropdown shows "You're all caught up".
  const agentUnreadCount = useMemo(
    () => notifications.filter((n) => !n.read).length,
    [notifications]
  )
  const persistentUnread = useMemo(
    () => wsNotifications.filter((n) => !n.read).length,
    [wsNotifications]
  )
  const unreadCount = agentUnreadCount + persistentUnread

  // Track WS notification ids seen so far and fire toasts only for new
  // arrivals. Seeded on the first snapshot so existing notifications don't
  // spam toasts when the page loads or the WS reconnects.
  // IMPORTANT: we must wait until snapshotReceived is true before seeding.
  // On first render wsNotifications is [] (WS not connected yet). Seeding
  // from that empty list means every notification in the real first
  // snapshot looks "new" and fires a stale toast (→1342 root cause).
  const seenNotifIdsRef = useRef<Set<string> | null>(null)

  useEffect(() => {
    // Do not seed until the first real snapshot has arrived.
    if (!snapshotReceived) return
    if (seenNotifIdsRef.current === null) {
      seenNotifIdsRef.current = new Set(wsNotifications.map((n) => n.id))
      return
    }
    const seen = seenNotifIdsRef.current
    for (const n of wsNotifications) {
      if (seen.has(n.id)) continue
      seen.add(n.id)
      if (!n.read && TOAST_WORTHY_PERSISTENT_TYPES.has(n.type)) {
        addPersistentToast({
          id: n.id,
          type: n.type,
          title: n.title ?? '',
          body: n.body ?? '',
          action_url: n.action_url ?? null,
        })
      }
    }
  }, [wsNotifications, addPersistentToast, snapshotReceived])

  // Re-render every minute so "X min ago" stays fresh
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(interval)
  }, [])

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
      useNotificationsStore.setState((s) => ({
        notifications: s.notifications.map((n) => n.id === id ? { ...n, read: true } : n),
      }))
    } catch {
      // ignore
    }
  }, [])

  const handleMarkAllPersistentRead = useCallback(async () => {
    try {
      await api.post('/notifications/read-all')
      useNotificationsStore.setState((s) => ({
        notifications: s.notifications.map((n) => ({ ...n, read: true })),
      }))
    } catch {
      // ignore
    }
  }, [])

  const handleOpenNotifications = () => {
    setShowNotifications(true)
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
    <>
    {/* Flow spacer: same height as the fixed header so page content never
        slides underneath it. Pages must NOT add their own top padding for
        the TopBar offset. This spacer is the single source of truth. */}
    <div className="h-14 sm:h-16 shrink-0" aria-hidden="true" data-testid="topbar-spacer" />
    <header
      className="fixed top-0 left-0 lg:left-56 h-14 sm:h-16 bg-white dark:bg-slate-950/80 backdrop-blur-md border-b border-slate-200 dark:border-slate-800/50 flex items-center justify-between px-4 sm:px-8 z-40 transition-[right] duration-200"
      style={{ right: chatOpen && isDesktop ? chatWidth : 0 }}
    >
      <div className="flex items-center gap-4 pl-10 lg:pl-0">
        <h1 className="text-lg font-semibold text-slate-900 dark:text-white" data-testid="topbar-title">{title}</h1>
      </div>

      <div data-tour="search" className="flex-1 max-w-md mx-2 sm:mx-8 hidden sm:block">
        <button
          onClick={() => setCommandPaletteOpen(true)}
          className="w-full relative group flex items-center bg-white dark:bg-slate-900 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-500 hover:text-slate-600 dark:hover:text-slate-400 transition-all cursor-pointer border border-transparent hover:border-slate-200 dark:hover:border-slate-700"
        >
          <Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-hover:text-blue-500" />
          <span>Search {displayOsName}</span>
          <kbd className="ml-auto text-[10px] font-mono text-slate-600 bg-slate-100 dark:bg-slate-800 rounded px-1.5 py-0.5 border border-slate-200 dark:border-slate-700">
            {`${modKey}K`}
          </kbd>
        </button>
      </div>

      <div className="flex items-center gap-2 sm:gap-4">
        {!tourComplete && (
          <button
            onClick={() => setShowTour(true)}
            className="flex items-center gap-1.5 px-2 py-1 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all text-xs"
            title="Start tour"
            data-testid="start-tour-btn"
          >
            <Icon name="play_circle" size={16} />
            <span className="hidden sm:inline">Start tour</span>
          </button>
        )}
        <button
          onClick={() => {
            navigate('/tasks')
            setTimeout(() => window.dispatchEvent(new CustomEvent('myos-quick-add-task')), 100)
          }}
          className="p-2.5 sm:p-2 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
          title="Add Task"
        >
          <Icon name="add_task" />
        </button>
        <button
          onClick={toggleChat}
          className="p-2.5 sm:p-2 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
          title={`Toggle Chat (${modKey}L)`}
        >
          <Icon name="chat" />
        </button>
        <div className="relative">
          <button
            onClick={handleOpenNotifications}
            className="relative p-2.5 sm:p-2 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
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
              <div className="absolute right-0 top-full mt-2 w-80 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xl z-50 overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
                  <span className="text-sm font-semibold text-white">Notifications</span>
                  <div className="flex items-center gap-3">
                    {unreadCount > 0 && (
                      <button
                        onClick={() => {
                          if (agentUnreadCount > 0) markAllRead()
                          if (persistentUnread > 0) handleMarkAllPersistentRead()
                        }}
                        className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
                      >
                        Mark all read
                      </button>
                    )}
                    {(notifications.length > 0 || wsNotifications.length > 0) && (
                      <button
                        onClick={clearAll}
                        className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
                      >
                        Clear all
                      </button>
                    )}
                    <button onClick={handleCloseNotifications} className="text-slate-500 hover:text-white">
                      <Icon name="close" size={16} />
                    </button>
                  </div>
                </div>

                {notifications.length === 0 && wsNotifications.length === 0 ? (
                  <div className="p-6 text-center">
                    <Icon name="notifications_none" size={32} className="text-slate-700 mb-2" />
                    <p className="text-sm text-slate-500">You&apos;re all caught up.</p>
                    <p className="text-xs text-slate-600 mt-1">Alerts from agents and updates will show up here.</p>
                  </div>
                ) : (
                  <div className="max-h-80 overflow-y-auto divide-y divide-slate-200 dark:divide-slate-800/50">
                    {wsNotifications.map((n) => (
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
                  <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
                    <span className="text-xs text-slate-600 dark:text-slate-400">Push notifications</span>
                    <button
                      onClick={handleTogglePush}
                      disabled={pushToggling}
                      className={`w-9 h-5 rounded-full relative transition-colors ${
                        pushEnabled ? 'accent-bg' : 'bg-slate-200 dark:bg-slate-700'
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
            <span className="text-xs text-amber-700 dark:text-amber-300">Offline</span>
          </div>
        )}
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold bg-gradient-to-br from-pink-500 to-purple-600">
          {displayOsName.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
    </>
  )
}
