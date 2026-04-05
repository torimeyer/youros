import { create } from 'zustand'

export interface AppNotification {
  id: string
  agentName: string
  prevStatus: string
  status: string
  timestamp: string
  read: boolean
}

interface NotificationStore {
  notifications: AppNotification[]
  toastIds: string[]
  addNotification: (agentName: string, prevStatus: string, status: string) => void
  dismissToast: (id: string) => void
  markAllRead: () => void
  clearAll: () => void
}

export const useNotificationStore = create<NotificationStore>((set) => ({
  notifications: [],
  toastIds: [],

  addNotification: (agentName, prevStatus, status) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const notification: AppNotification = {
      id,
      agentName,
      prevStatus,
      status,
      timestamp: new Date().toISOString(),
      read: false,
    }
    set((s) => ({
      notifications: [notification, ...s.notifications].slice(0, 50),
      toastIds: [...s.toastIds, id],
    }))
    // Auto-dismiss toast after 5s
    setTimeout(() => {
      set((s) => ({ toastIds: s.toastIds.filter((t) => t !== id) }))
    }, 5000)
  },

  dismissToast: (id) =>
    set((s) => ({ toastIds: s.toastIds.filter((t) => t !== id) })),

  markAllRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
    })),

  clearAll: () => set({ notifications: [], toastIds: [] }),
}))
