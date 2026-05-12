import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useNotificationsStore, type Notification } from '../stores/notificationsStore'
import { api } from '../lib/api'

const FALLBACK_POLL_MS = 5000
const FALLBACK_DELAY_MS = 3000

interface NotificationsFrame {
  snapshot?: Notification[]
  delta?: Notification
}

export function useNotificationsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/notifications', true)
  const setNotifications = useNotificationsStore((s) => s.setNotifications)
  const setWsConnected = useNotificationsStore((s) => s.setWsConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as NotificationsFrame
    if (Array.isArray(frame.snapshot)) {
      setNotifications(frame.snapshot)
    } else if (frame.delta) {
      useNotificationsStore.setState((state) => {
        const idx = state.notifications.findIndex((n) => n.id === frame.delta!.id)
        if (idx !== -1) {
          const updated = [...state.notifications]
          updated[idx] = frame.delta!
          return { notifications: updated }
        }
        return { notifications: [frame.delta!, ...state.notifications] }
      })
    }
  }, [lastMessage, setNotifications])

  useEffect(() => {
    setWsConnected(isConnected)

    if (isConnected) {
      if (fallbackTimerRef.current !== null) {
        clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
      if (pollRef.current !== null) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }

    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(async () => {
        try {
          const data = await api.get<Notification[]>('/notifications')
          setNotifications(Array.isArray(data) ? data : [])
        } catch {
          // network error during fallback — ignore, retry next interval
        }
      }, FALLBACK_POLL_MS)
    }, FALLBACK_DELAY_MS)

    return () => {
      if (fallbackTimerRef.current !== null) {
        clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
    }
  }, [isConnected, setWsConnected, setNotifications])

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
