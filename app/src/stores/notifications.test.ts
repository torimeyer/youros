import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useNotificationStore } from './notifications'

// Reset the zustand store between tests so firedKeys, notifications, and
// toastIds do not leak across cases.
beforeEach(() => {
  useNotificationStore.setState({
    notifications: [],
    toastIds: [],
    firedKeys: new Set<string>(),
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
})
