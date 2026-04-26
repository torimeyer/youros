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

    it('test_roadmap_ready_per_session_gate_blocks_second', () => {
      // roadmap_ready is per-session gated: only the first one shows,
      // subsequent ones (even with different content) are suppressed so
      // file-watch re-fires don't spam the user.
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

      expect(useNotificationStore.getState().notifications).toHaveLength(1)
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

    describe('cross-path dedup (agent persistent ↔ addNotification)', () => {
      it('test_agent_persistent_toast_suppressed_when_addNotification_fired_first', () => {
        // Regression: Agents.tsx fires addNotification on the running→completed
        // transition, then TopBar fires addPersistentToast for the backend
        // "agent" row with the same agent. Before the fix both toasted.
        const { addNotification, addPersistentToast } = useNotificationStore.getState()

        addNotification({ name: 'roadmap-agent', source: 'claude-code' }, 'running', 'completed')
        addPersistentToast({
          id: 'agent-notif-1',
          type: 'agent',
          title: 'Agent done: roadmap-agent',
          body: 'Roadmap generation finished.',
          action_url: '/agents',
        })

        const s = useNotificationStore.getState()
        // Only one toast — the addNotification one.
        expect(s.notifications).toHaveLength(1)
        expect(s.toastIds).toHaveLength(1)
        // The persistent notification id must be recorded so future polls skip it.
        expect(s.persistentToastIds.has('agent-notif-1')).toBe(true)
      })

      it('test_addNotification_suppressed_when_agent_persistent_toast_fired_first', () => {
        // Opposite order: TopBar poll fires addPersistentToast before Agents.tsx
        // detects the transition. addNotification should be a no-op.
        const { addNotification, addPersistentToast } = useNotificationStore.getState()

        addPersistentToast({
          id: 'agent-notif-2',
          type: 'agent',
          title: 'Agent done: roadmap-agent',
          body: 'Roadmap generation finished.',
          action_url: '/agents',
        })
        addNotification({ name: 'roadmap-agent', source: 'claude-code' }, 'running', 'completed')

        const s = useNotificationStore.getState()
        expect(s.notifications).toHaveLength(1)
        expect(s.toastIds).toHaveLength(1)
      })

      it('different agent names do not interfere with each other', () => {
        // Cross-path dedup must be keyed by agent name — agent-a completing
        // must not suppress a persistent toast for agent-b.
        const { addNotification, addPersistentToast } = useNotificationStore.getState()

        addNotification({ name: 'agent-a', source: 'claude-code' }, 'running', 'completed')
        addPersistentToast({
          id: 'agent-notif-3',
          type: 'agent',
          title: 'Agent done: agent-b',
          body: 'agent-b finished.',
          action_url: '/agents',
        })

        const s = useNotificationStore.getState()
        expect(s.notifications).toHaveLength(2)
        expect(s.toastIds).toHaveLength(2)
      })

      it('agent persistent toast without "Agent done:" prefix is not cross-deduped', () => {
        // Only titles matching the known prefix can have the agent name
        // extracted. Custom titles fall through and toast normally.
        const { addNotification, addPersistentToast } = useNotificationStore.getState()

        addNotification({ name: 'some-agent', source: 'claude-code' }, 'running', 'completed')
        addPersistentToast({
          id: 'agent-notif-4',
          type: 'agent',
          title: 'Custom system event',
          body: 'Something happened.',
          action_url: null,
        })

        const s = useNotificationStore.getState()
        // Both toast: addNotification fired, and the persistent one has no
        // extractable agent name so cross-dedup does not apply.
        expect(s.notifications).toHaveLength(2)
      })

      it('roadmap_ready type is not affected by cross-path dedup', () => {
        // Only type="agent" rows trigger cross-path dedup. roadmap_ready,
        // spec_complete, etc. always toast regardless of firedKeys state.
        // Note: roadmap-agent IS a system maintenance agent, so addNotification
        // suppresses it. The roadmap_ready persistent toast still fires once.
        const { addNotification, addPersistentToast } = useNotificationStore.getState()

        addNotification({ name: 'roadmap-agent', source: 'claude-code' }, 'running', 'completed')
        addPersistentToast({
          id: 'roadmap-notif-1',
          type: 'roadmap_ready',
          title: 'Your roadmap is ready',
          body: 'Open roadmap.md',
          action_url: '/files',
        })

        const s = useNotificationStore.getState()
        // addNotification is suppressed (roadmap-agent is a system agent).
        // Only the roadmap_ready persistent toast fires.
        expect(s.notifications).toHaveLength(1)
        expect(s.toastIds).toHaveLength(1)
        expect(s.notifications[0].status).toBe('roadmap_ready')
      })
    })
  })

  describe('system maintenance agent suppression', () => {
    const systemAgentCases: [string, string][] = [
      ['dupe-guard-abc123', 'dupe-guard-*'],
      ['stale-complete-xyz', 'stale-complete-*'],
      ['reaper-stale-20240426', 'reaper-*'],
      ['roadmap-weekly', 'roadmap-*'],
      ['brainstorm-feature-xyz', 'brainstorm-*'],
    ]

    it.each(systemAgentCases)(
      'suppresses toast for system agent %s (matches %s pattern)',
      (name) => {
        useNotificationStore.getState().addNotification(
          { name, source: 'claude-code' },
          'running',
          'completed',
        )
        const s = useNotificationStore.getState()
        expect(s.notifications).toHaveLength(0)
        expect(s.toastIds).toHaveLength(0)
      },
    )

    it('still fires toast for user-spawned agent not matching system patterns', () => {
      useNotificationStore.getState().addNotification(
        { name: 'fix-login-bug', source: 'claude-code' },
        'running',
        'completed',
      )
      const s = useNotificationStore.getState()
      expect(s.notifications).toHaveLength(1)
      expect(s.toastIds).toHaveLength(1)
      expect(s.notifications[0].agentName).toBe('fix-login-bug')
    })

    it('suppresses system agents even when they would otherwise pass isUserSpawnedAgent', () => {
      // A dupe-guard agent registered with source=claude-code would pass
      // isUserSpawnedAgent, but the name pattern guard fires first.
      useNotificationStore.getState().addNotification(
        { name: 'dupe-guard-run-1', source: 'claude-code' },
        'running',
        'completed',
      )
      expect(useNotificationStore.getState().notifications).toHaveLength(0)
    })
  })

  describe('roadmap_ready session gate', () => {
    it('fires only once per session even with different content', () => {
      const { addPersistentToast } = useNotificationStore.getState()

      addPersistentToast({
        id: 'rr-1',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'First roadmap version',
        action_url: '/files',
      })
      addPersistentToast({
        id: 'rr-2',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'Second roadmap version — different content',
        action_url: '/files',
      })

      const s = useNotificationStore.getState()
      expect(s.notifications).toHaveLength(1)
      expect(s.toastIds).toHaveLength(1)
      // Both ids recorded so future polls skip them.
      expect(s.persistentToastIds.has('rr-1')).toBe(true)
      expect(s.persistentToastIds.has('rr-2')).toBe(true)
      expect(s.persistentToastIds.has('type-gate:roadmap_ready')).toBe(true)
    })

    it('clearAll resets the roadmap_ready gate so a new session can toast', () => {
      const { addPersistentToast, clearAll } = useNotificationStore.getState()

      addPersistentToast({
        id: 'rr-3',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'First body',
        action_url: '/files',
      })
      expect(useNotificationStore.getState().notifications).toHaveLength(1)

      clearAll()
      expect(useNotificationStore.getState().persistentToastIds.size).toBe(0)

      addPersistentToast({
        id: 'rr-4',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'Post-clearAll body',
        action_url: '/files',
      })
      expect(useNotificationStore.getState().notifications).toHaveLength(1)
    })

    it('other notification types are not affected by the roadmap gate', () => {
      const { addPersistentToast } = useNotificationStore.getState()

      // Fire a roadmap_ready first (arms the gate)
      addPersistentToast({
        id: 'rr-5',
        type: 'roadmap_ready',
        title: 'Roadmap ready',
        body: 'Some body',
        action_url: '/files',
      })
      // spec_complete is a different type and must still toast
      addPersistentToast({
        id: 'sc-1',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Build done.',
        action_url: '/specs',
      })

      expect(useNotificationStore.getState().notifications).toHaveLength(2)
    })
  })
})
