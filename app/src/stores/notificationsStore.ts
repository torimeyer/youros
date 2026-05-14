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
  /** True after the first WS snapshot (or first successful REST poll)
   *  has been delivered. TopBar gates its seenNotifIdsRef seeding on
   *  this flag so it does not accidentally seed from an empty list and
   *  then toast every notification in the real first snapshot as if it
   *  were new. */
  snapshotReceived: boolean
  setNotifications: (notifications: Notification[]) => void
  setWsConnected: (connected: boolean) => void
  setSnapshotReceived: (received: boolean) => void
}

export const useNotificationsStore = create<NotificationsState>((set) => ({
  notifications: [],
  wsConnected: false,
  snapshotReceived: false,
  setNotifications: (notifications) => set({ notifications }),
  setWsConnected: (connected) => set({ wsConnected: connected }),
  setSnapshotReceived: (received) => set({ snapshotReceived: received }),
}))
