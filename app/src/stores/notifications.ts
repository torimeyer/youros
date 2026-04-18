import { create } from 'zustand'
import { isUserSpawnedAgent } from '../lib/agentUtils'

export interface AppNotification {
  id: string
  agentName: string
  prevStatus: string
  status: string
  timestamp: string
  read: boolean
}

/**
 * Minimal agent shape needed to decide whether a status-change toast
 * should fire. Accepts any object with a name and optional source/model;
 * the store forwards it to ``isUserSpawnedAgent`` from agentUtils so the
 * filter stays in one place.
 */
export interface NotificationAgent {
  name: string
  source?: string
  model?: string
  description?: string
}

/**
 * Returns true when a status-change toast for this agent should be
 * suppressed. Chat sessions, audit-log entries, hook auto-files, and the
 * main interactive Claude Code session are NOT user-spawned agents and
 * must never fire "Agent finished" toasts. This also skips any agent
 * whose name starts with "chat-" as a belt-and-braces guard (chat-default
 * is the session Tori saw spamming her screen).
 */
export function shouldSuppressAgentToast(agent: NotificationAgent): boolean {
  if (agent.name.startsWith('chat-')) return true
  if (!isUserSpawnedAgent(agent)) return true
  return false
}

interface NotificationStore {
  notifications: AppNotification[]
  toastIds: string[]
  /** Set of "{agentName}:{status}" keys that have already fired a toast
   *  in this session. Prevents a chatty backend that reports the same
   *  terminal transition more than once from spamming the toast stack.
   */
  firedKeys: Set<string>
  addNotification: (
    agent: NotificationAgent,
    prevStatus: string,
    status: string,
  ) => void
  dismissToast: (id: string) => void
  markAllRead: () => void
  clearAll: () => void
}

export const useNotificationStore = create<NotificationStore>((set, get) => ({
  notifications: [],
  toastIds: [],
  firedKeys: new Set<string>(),

  addNotification: (agent, prevStatus, status) => {
    // Bug 1: never fire "Agent finished" toasts for chat sessions, audit
    // rows, hook auto-files, or subscription chat rows. They are not
    // user-spawned agents. See feedback_chat_not_agent.md.
    if (shouldSuppressAgentToast(agent)) return

    // Bug 2: never fire the same terminal toast twice for the same agent.
    // The backend can briefly flap a status or re-send the same row, and
    // we do not want the toast stack to pile up.
    const dedupeKey = `${agent.name}:${status}`
    if (get().firedKeys.has(dedupeKey)) return

    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const notification: AppNotification = {
      id,
      agentName: agent.name,
      prevStatus,
      status,
      timestamp: new Date().toISOString(),
      read: false,
    }
    set((s) => {
      const firedKeys = new Set(s.firedKeys)
      firedKeys.add(dedupeKey)
      return {
        notifications: [notification, ...s.notifications].slice(0, 50),
        toastIds: [...s.toastIds, id],
        firedKeys,
      }
    })
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

  clearAll: () =>
    set({ notifications: [], toastIds: [], firedKeys: new Set<string>() }),
}))
