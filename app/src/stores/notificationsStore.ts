import { create } from 'zustand'

export interface Notification {
  id: string
  type: string
  title: string
  body: string
  created_at: string
  read: boolean
  action_label?: string | null
  action_url?: string | null
}

interface NotificationsState {
  notifications: Notification[]
  ws_connected: boolean
  setNotifications: (notifications: Notification[]) => void
  setWsConnected: (ws_connected: boolean) => void
}

export const useNotificationsStore = create<NotificationsState>((set) => ({
  notifications: [],
  ws_connected: false,
  setNotifications: (notifications) => set({ notifications }),
  setWsConnected: (ws_connected) => set({ ws_connected }),
}))
