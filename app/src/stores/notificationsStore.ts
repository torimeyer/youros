import { create } from 'zustand'

export interface Notification {
  id: string
  type: string
  title?: string
  body?: string
  action_label?: string | null
  action_url?: string | null
  read?: boolean
  created_at?: string
  metadata?: Record<string, unknown>
}

interface NotificationsState {
  notifications: Notification[]
  wsConnected: boolean
  setNotifications: (notifications: Notification[]) => void
  setWsConnected: (connected: boolean) => void
}

export const useNotificationsStore = create<NotificationsState>((set) => ({
  notifications: [],
  wsConnected: false,
  setNotifications: (notifications) => set({ notifications }),
  setWsConnected: (connected) => set({ wsConnected: connected }),
}))
