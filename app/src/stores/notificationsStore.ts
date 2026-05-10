import { create } from 'zustand'

interface Notification {
  id: string
  type: string
  title?: string
  message?: string
  created_at?: string
  read?: boolean
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
