import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useNotificationStore } from './notifications'

// Reset the zustand store between tests so firedKeys, notifications, and
// toastIds do not leak across cases.
beforeEach(() => {
  useNotificationStore.setState({
    notifications: [],
    toastIds: [],
    firedKeys: new Set<string>(),
    persistentToastIds: new Set<string>(),
    lastFeatureLive: null,
  })
  vi.useRealTimers()
})

describe('notifications store', () => {
  it('test_finished_toast_does_not_fire_for_chat_session', () => {
    // Chat-turn agents (source === "chat") are not user-spawned agents.
    // Per feedback_chat_not_agent.md they must never trigger the
    // "Agent finished" toast.
    const before = useNotificationStore.getState()
    before.addNotification(
      { name: 'chat-default', source: 'chat' },
      'running',
      'completed',
    )

    const after = useNotificationStore.getState()
    expect(after.notifications).toHaveLength(0)
    expect(after.toastIds).toHaveLength(0)
  })

  it('suppresses toasts for any agent whose name starts with chat-', () => {
    // Belt-and-braces guard: even if the source field is missing, any
    // chat-* named row is still a chat session and must be silent.
    useNotificationStore.getState().addNotification(
      { name: 'chat-default' },
      'running',
      'completed',
    )

    const s = useNotificationStore.getState()
    expect(s.notifications).toHaveLength(0)
    expect(s.toastIds).toHaveLength(0)
  })

  it('suppresses toasts for audit-source rows', () => {
    useNotificationStore.getState().addNotification(
      { name: 'audit-123', source: 'audit' },
      'running',
      'completed',
    )

    expect(useNotificationStore.getState().notifications).toHaveLength(0)
  })

  it('suppresses toasts for hook-source rows', () => {
    useNotificationStore.getState().addNotification(
      { name: 'hook-abc', source: 'hook' },
      'running',
      'completed',
    )

    expect(useNotificationStore.getState().notifications).toHaveLength(0)
  })

  it('suppresses toasts for subscription chat rows', () => {
    useNotificationStore.getState().addNotification(
      { name: 'sub-xyz', model: 'claude-code-subscription' },
      'running',
      'completed',
    )

    expect(useNotificationStore.getState().notifications).toHaveLength(0)
  })

  it('test_finished_toast_fires_only_once_per_agent', () => {
    // The backend can flap a terminal status or re-send the same row.
    // Only one toast should appear for a given (agentName, status) pair.
    const { addNotification } = useNotificationStore.getState()

    addNotification({ name: 'my-agent', source: 'claude-code' }, 'running', 'completed')
    addNotification({ name: 'my-agent', source: 'claude-code' }, 'running', 'completed')
    addNotification({ name: 'my-agent', source: 'claude-code' }, 'running', 'completed')

    const s = useNotificationStore.getState()
    expect(s.notifications).toHaveLength(1)
    expect(s.toastIds).toHaveLength(1)
    expect(s.notifications[0].agentName).toBe('my-agent')
    expect(s.notifications[0].status).toBe('completed')
  })

  it('fires for a real user-spawned agent finishing', () => {
    useNotificationStore.getState().addNotification(
      { name: 'fix-403-bug', source: 'claude-code' },
      'running',
      'completed',
    )

    const s = useNotificationStore.getState()
    expect(s.notifications).toHaveLength(1)
    expect(s.toastIds).toHaveLength(1)
    expect(s.notifications[0].agentName).toBe('fix-403-bug')
  })

  it('allows different terminal statuses for the same agent', () => {
    // Dedupe is keyed by (name, status), so an agent that somehow hits
    // both "completed" and "failed" should still get both toasts. In
    // practice this never happens, but the key shape should permit it.
    const { addNotification } = useNotificationStore.getState()
    addNotification({ name: 'agent-a', source: 'claude-code' }, 'running', 'completed')
    addNotification({ name: 'agent-a', source: 'claude-code' }, 'running', 'failed')

    expect(useNotificationStore.getState().notifications).toHaveLength(2)
  })

  it('clearAll resets firedKeys so a later run can toast again', () => {
    const { addNotification, clearAll } = useNotificationStore.getState()
    addNotification({ name: 'agent-a', source: 'claude-code' }, 'running', 'completed')
    expect(useNotificationStore.getState().notifications).toHaveLength(1)

    clearAll()
    expect(useNotificationStore.getState().notifications).toHaveLength(0)
    expect(useNotificationStore.getState().firedKeys.size).toBe(0)

    addNotification({ name: 'agent-a', source: 'claude-code' }, 'running', 'completed')
    expect(useNotificationStore.getState().notifications).toHaveLength(1)
  })

  describe('addPersistentToast', () => {
    it('pushes a toast for a new persistent notification id', () => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-1',
        type: 'roadmap_ready',
        title: 'Your roadmap is ready',
        body: 'See /files/roadmap.md',
        action_url: '/files',
      })

      const s = useNotificationStore.getState()
      expect(s.notifications).toHaveLength(1)
      expect(s.toastIds).toContain('notif-1')
      expect(s.notifications[0].agentName).toBe('Your roadmap is ready')
      expect(s.notifications[0].status).toBe('roadmap_ready')
      expect(s.persistentToastIds.has('notif-1')).toBe(true)
    })

    it('does NOT push twice for the same persistent id', () => {
      const { addPersistentToast } = useNotificationStore.getState()
      const payload = {
        id: 'notif-2',
        type: 'agent',
        title: 'Agent finished',
        body: 'fix-bug completed',
        action_url: null,
      }
      addPersistentToast(payload)
      addPersistentToast(payload)
      addPersistentToast(payload)

      const s = useNotificationStore.getState()
      expect(s.notifications).toHaveLength(1)
      expect(s.toastIds.filter((id) => id === 'notif-2')).toHaveLength(1)
    })

    it('auto-dismisses the toast after 5 seconds', () => {
      vi.useFakeTimers()
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-3',
        type: 'roadmap_ready',
        title: 'Ready',
        body: '',
        action_url: null,
      })
      expect(useNotificationStore.getState().toastIds).toContain('notif-3')

      vi.advanceTimersByTime(5000)
      expect(useNotificationStore.getState().toastIds).not.toContain('notif-3')
      // The notification row itself lives on in the drawer; only the
      // toast is dismissed.
      expect(useNotificationStore.getState().notifications).toHaveLength(1)
      vi.useRealTimers()
    })

    it('clearAll resets persistentToastIds so the same row can toast again', () => {
      const { addPersistentToast, clearAll } = useNotificationStore.getState()
      addPersistentToast({
        id: 'notif-4',
        type: 'roadmap_ready',
        title: 'Ready',
        body: '',
        action_url: null,
      })
      expect(useNotificationStore.getState().notifications).toHaveLength(1)

      clearAll()
      expect(useNotificationStore.getState().persistentToastIds.size).toBe(0)

      addPersistentToast({
        id: 'notif-4',
        type: 'roadmap_ready',
        title: 'Ready',
        body: '',
        action_url: null,
      })
      expect(useNotificationStore.getState().notifications).toHaveLength(1)
    })

    it('test_duplicate_content_with_different_ids_collapses_to_one_toast', () => {
      // Root-cause regression: the backend was fanning out a single
      // "Roadmap ready" completion into several notification rows with
      // different ids but identical content. The store's id-only dedupe
      // let each one through, so the user saw three toasts back-to-back.
      // With the content-level dedupe, only the first one toasts.
      const { addPersistentToast } = useNotificationStore.getState()
      addPersistentToast({
        id: 'roadmap-1',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: "Open roadmap.md. Type 'create tasks' in chat to break it down.",
        action_url: '/files',
      })
      addPersistentToast({
        id: 'roadmap-2',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: "Open roadmap.md. Type 'create tasks' in chat to break it down.",
        action_url: '/files',
      })
      addPersistentToast({
        id: 'roadmap-3',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: "Open roadmap.md. Type 'create tasks' in chat to break it down.",
        action_url: '/files',
      })

      const s = useNotificationStore.getState()
      expect(s.notifications).toHaveLength(1)
      expect(s.toastIds).toHaveLength(1)
      // All three ids must be recorded so a later poll does not replay them.
      expect(s.persistentToastIds.has('roadmap-1')).toBe(true)
      expect(s.persistentToastIds.has('roadmap-2')).toBe(true)
      expect(s.persistentToastIds.has('roadmap-3')).toBe(true)
    })

    it('test_different_content_still_toasts_after_first_one', () => {
      // The content-level dedupe must not suppress a later, legitimately-new
      // notification whose title or body differs from the first one.
      const { addPersistentToast } = useNotificationStore.getState()
      addPersistentToast({
        id: 'a',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'First body',
        action_url: '/files',
      })
      addPersistentToast({
        id: 'b',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'A completely different body',
        action_url: '/files',
      })

      expect(useNotificationStore.getState().notifications).toHaveLength(2)
    })

    it('test_persistent_toast_stores_body_on_notification_entry', () => {
      // The Toast renderer reads ``notification.body`` to show a human
      // message like "Open roadmap.md. Type 'create tasks' ...". Without
      // it the toast falls back to "Agent roadmap_ready" (the bug the
      // user reported).
      useNotificationStore.getState().addPersistentToast({
        id: 'body-test',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: "Open roadmap.md. Type 'create tasks' in chat to break it down.",
        action_url: '/files',
      })

      const n = useNotificationStore.getState().notifications[0]
      expect(n.body).toBe(
        "Open roadmap.md. Type 'create tasks' in chat to break it down.",
      )
      expect(n.agentName).toBe('Roadmap ready')
    })

    it('stamps lastFeatureLive when a spec_complete toast arrives', () => {
      // Regression for the silent-landing bug: when the backend fires a
      // spec_complete persistent notification (Build-it just shipped),
      // the store must stamp lastFeatureLive so the ChatPanel's All
      // pill pulse and the release-notes modal fallback both fire.
      // Without this stamp, the TopBar toast pops in isolation and the
      // pill + modal stay silent.
      expect(useNotificationStore.getState().lastFeatureLive).toBeNull()

      useNotificationStore.getState().addPersistentToast({
        id: 'fl-1',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Multi model chat is built and ready to try.',
        action_url: '/specs?expand=docs/spec/multi-model.md',
      })

      const live = useNotificationStore.getState().lastFeatureLive
      expect(live).not.toBeNull()
      expect(live?.id).toBe('fl-1')
      expect(live?.title).toBe('Your feature is live')
      expect(live?.body).toBe('Multi model chat is built and ready to try.')
      expect(typeof live?.at).toBe('number')
    })

    it('leaves lastFeatureLive untouched for non-spec_complete toast types', () => {
      // Guard: the feature-live stamp must not fire on generic agent
      // completion toasts. Otherwise the pill would pulse every time
      // any agent finishes its work.
      useNotificationStore.getState().addPersistentToast({
        id: 'agent-1',
        type: 'agent',
        title: 'Agent done: foo',
        body: 'Agent finished',
        action_url: '/agents',
      })
      expect(useNotificationStore.getState().lastFeatureLive).toBeNull()
    })

    it('clearAll resets lastFeatureLive so a fresh demo can re-pulse', () => {
      useNotificationStore.getState().addPersistentToast({
        id: 'fl-2',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: '',
        action_url: null,
      })
      expect(useNotificationStore.getState().lastFeatureLive).not.toBeNull()
      useNotificationStore.getState().clearAll()
      expect(useNotificationStore.getState().lastFeatureLive).toBeNull()
    })
  })
})
