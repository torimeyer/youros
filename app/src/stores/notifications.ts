import { create } from 'zustand'
import { isUserSpawnedAgent, isSystemMaintenanceAgent } from '../lib/agentUtils'

export interface AppNotification {
  id: string
  agentName: string
  prevStatus: string
  status: string
  timestamp: string
  read: boolean
  /** Human-readable body shown under the title in the toast. Set when
   *  the notification originates from a backend-persistent row so the
   *  toast can show "Open roadmap.md. Type 'create tasks' in chat to
   *  break it down." instead of the raw type string. Agent
   *  status-change toasts leave this undefined and fall back to the
   *  generic "Agent finished / failed / ..." message. */
  body?: string
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
 * is the session Tori saw spamming her screen), and any background
 * maintenance agent (dupe-guard-*, stale-complete-*, reaper-*, etc.)
 * whose completions are never user-actionable.
 */
export function shouldSuppressAgentToast(agent: NotificationAgent): boolean {
  if (agent.name.startsWith('chat-')) return true
  if (isSystemMaintenanceAgent(agent.name)) return true
  if (!isUserSpawnedAgent(agent)) return true
  return false
}

/**
 * Shape of a persistent backend notification (the kind served by
 * ``GET /api/notifications``) at the minimum set of fields we need to
 * render a toast. Anything beyond id/type/title/body/action_url is
 * ignored by the toast layer.
 */
export interface PersistentToastInput {
  id: string
  type: string
  title: string
  body: string
  action_url?: string | null
}

interface NotificationStore {
  notifications: AppNotification[]
  toastIds: string[]
  /** Set of "{agentName}:{status}" keys that have already fired a toast
   *  in this session. Prevents a chatty backend that reports the same
   *  terminal transition more than once from spamming the toast stack.
   */
  firedKeys: Set<string>
  /** Set of persistent notification ids that have already been surfaced
   *  as a toast. Keeps the TopBar poll loop from re-firing the same row
   *  on every tick.
   */
  persistentToastIds: Set<string>
  /** Last persistent notification of type ``spec_complete`` seen by the
   *  store. Holds the notification id, spec path (when the backend
   *  provided one on the toast input, carried on the AppNotification
   *  ``body``/``agentName``), and a monotonic ``at`` timestamp used by
   *  the ChatPanel to drive its one-time "New" pulse on the All pill
   *  after a Build-it landing. ``null`` until the first spec_complete
   *  arrives in this session. */
  lastFeatureLive: { id: string; title: string; body: string; at: number; action_url?: string | null } | null
  addNotification: (
    agent: NotificationAgent,
    prevStatus: string,
    status: string,
  ) => void
  /** Push a backend-persistent notification (e.g. ``roadmap_ready``,
   *  ``agent``) onto the toast stack. Dedupes on ``notif.id`` so the
   *  same row polled twice does not toast twice. */
  addPersistentToast: (notif: PersistentToastInput) => void
  dismissToast: (id: string) => void
  markAllRead: () => void
  clearAll: () => void
}

export const useNotificationStore = create<NotificationStore>((set, get) => ({
  notifications: [],
  toastIds: [],
  firedKeys: new Set<string>(),
  persistentToastIds: new Set<string>(),
  lastFeatureLive: null,

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

  addPersistentToast: (notif) => {
    // Dedupe on the backend-assigned id so the TopBar poll can call this
    // repeatedly without stacking duplicate toasts. The id is stable
    // across polls for a given notification row.
    if (get().persistentToastIds.has(notif.id)) return

    // Gate for roadmap_ready: the backend can fire a new notification row
    // on every file-watch update of roadmap.md. Content dedup only helps
    // when the body is identical; if the body varies, the user would see
    // a toast per update. Fire at most one roadmap_ready toast per session.
    if (notif.type === 'roadmap_ready') {
      const typeGateKey = 'type-gate:roadmap_ready'
      if (get().persistentToastIds.has(typeGateKey)) {
        set((s) => {
          const persistentToastIds = new Set(s.persistentToastIds)
          persistentToastIds.add(notif.id)
          return { persistentToastIds }
        })
        return
      }
    }

    // Second line of defence: if the BACKEND produced several rows with
    // different ids but identical (type + title + body) content in quick
    // succession (e.g. an agent completion code path fires the same
    // notification from multiple save hooks), collapse them to a single
    // toast. Without this, the user sees "Roadmap ready" three times in
    // a row. The content key is recorded in ``persistentToastIds`` so a
    // later, legitimately-new notification with different content still
    // toasts on its own.
    const contentKey = `content:${notif.type}|${notif.title}|${notif.body}`
    if (get().persistentToastIds.has(contentKey)) {
      // Still record the id so we do not reconsider it on the next poll.
      set((s) => {
        const persistentToastIds = new Set(s.persistentToastIds)
        persistentToastIds.add(notif.id)
        return { persistentToastIds }
      })
      return
    }

    // Cross-path dedup: the backend creates an "agent"-type persistent
    // notification for every agent completion AND the Agents page fires
    // addNotification on the same running→terminal transition. Both use
    // firedKeys (keyed by "{agentName}:{status}") but they don't share
    // that check across paths, so the user was seeing two toasts for one
    // event. This block bridges the two paths:
    //   - If addNotification already fired (firedKeys has the key): record
    //     the persistent id and exit so no duplicate toast appears.
    //   - If we're firing first: pre-register all terminal statuses in
    //     firedKeys so the later addNotification call skips the duplicate.
    if (notif.type === 'agent') {
      const agentDonePrefix = 'Agent done: '
      const agentNameFromTitle = notif.title.startsWith(agentDonePrefix)
        ? notif.title.slice(agentDonePrefix.length)
        : null
      if (agentNameFromTitle) {
        const terminalStatuses = [
          'completed', 'completed_timeout', 'cancelled', 'failed',
          'terminated_stale', 'stopped', 'abandoned', 'killed',
        ]
        const alreadyFired = terminalStatuses.some(
          (st) => get().firedKeys.has(`${agentNameFromTitle}:${st}`)
        )
        if (alreadyFired) {
          // addNotification got here first — skip and record the id.
          set((s) => {
            const persistentToastIds = new Set(s.persistentToastIds)
            persistentToastIds.add(notif.id)
            persistentToastIds.add(contentKey)
            return { persistentToastIds }
          })
          return
        }
        // We're firing first — pre-register so addNotification skips it.
        set((s) => {
          const firedKeys = new Set(s.firedKeys)
          terminalStatuses.forEach((st) => firedKeys.add(`${agentNameFromTitle}:${st}`))
          return { firedKeys }
        })
      }
    }

    // Reuse the AppNotification shape so the existing NotificationToasts
    // renderer (which reads from notifications + toastIds) picks it up
    // without a second render path. agentName holds the human-readable
    // title; body holds the human-readable message so the toast renders
    // "Open roadmap.md. Type 'create tasks' ..." instead of the raw
    // type string; status holds the notification type which drives icon
    // choice in NotificationToast (unknown types fall through to the
    // info icon).
    const toastEntry: AppNotification = {
      id: notif.id,
      agentName: notif.title || notif.body || notif.type,
      prevStatus: '',
      status: notif.type,
      timestamp: new Date().toISOString(),
      read: false,
      body: notif.body || undefined,
    }
    set((s) => {
      const persistentToastIds = new Set(s.persistentToastIds)
      persistentToastIds.add(notif.id)
      persistentToastIds.add(contentKey)
      // Arm the per-session gate so subsequent roadmap_ready rows (fired by
      // file-watch events with varying content) are silenced.
      if (notif.type === 'roadmap_ready') {
        persistentToastIds.add('type-gate:roadmap_ready')
      }
      const nextState: Partial<NotificationStore> = {
        notifications: [toastEntry, ...s.notifications].slice(0, 50),
        toastIds: [...s.toastIds, notif.id],
        persistentToastIds,
      }
      // Stamp lastFeatureLive the moment a spec_complete row lands so
      // ChatPanel can pulse the All pill and ReleaseNotesWatcher can
      // open the modal even when /api/specs is empty (which happens
      // whenever the spec file has been cleaned up after the build).
      if (notif.type === 'spec_complete') {
        nextState.lastFeatureLive = {
          id: notif.id,
          title: notif.title || 'Your feature is live',
          body: notif.body || '',
          at: Date.now(),
          action_url: notif.action_url,
        }
      }
      return nextState as NotificationStore
    })
    // Auto-dismiss after 5s to match the existing agent-finished toast.
    setTimeout(() => {
      set((s) => ({ toastIds: s.toastIds.filter((t) => t !== notif.id) }))
    }, 5000)
  },

  dismissToast: (id) =>
    set((s) => ({ toastIds: s.toastIds.filter((t) => t !== id) })),

  markAllRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
    })),

  clearAll: () =>
    set({
      notifications: [],
      toastIds: [],
      firedKeys: new Set<string>(),
      persistentToastIds: new Set<string>(),
      lastFeatureLive: null,
    }),
}))
