import { describe, it, expect, beforeEach } from 'vitest'
import { useNotificationsStore } from './notificationsStore'

const NOTIFICATION = {
  id: 'n-1',
  type: 'task_completed',
  title: 'Task done',
  body: 'Your task finished successfully.',
  created_at: '2026-05-10T00:00:00Z',
  read: false,
}

describe('useNotificationsStore (→1133)', () => {
  beforeEach(() => {
    useNotificationsStore.setState({ notifications: [], ws_connected: false })
  })

  it('setNotifications replaces notifications list', () => {
    useNotificationsStore.getState().setNotifications([NOTIFICATION])
    expect(useNotificationsStore.getState().notifications).toHaveLength(1)
    expect(useNotificationsStore.getState().notifications[0].id).toBe('n-1')
  })

  it('setNotifications with empty array clears notifications', () => {
    useNotificationsStore.getState().setNotifications([NOTIFICATION])
    useNotificationsStore.getState().setNotifications([])
    expect(useNotificationsStore.getState().notifications).toHaveLength(0)
  })

  it('setWsConnected flips ws_connected flag', () => {
    expect(useNotificationsStore.getState().ws_connected).toBe(false)
    useNotificationsStore.getState().setWsConnected(true)
    expect(useNotificationsStore.getState().ws_connected).toBe(true)
    useNotificationsStore.getState().setWsConnected(false)
    expect(useNotificationsStore.getState().ws_connected).toBe(false)
  })

  it('subsequent setNotifications overwrites previous state', () => {
    useNotificationsStore.getState().setNotifications([NOTIFICATION])
    const n2 = { ...NOTIFICATION, id: 'n-2', title: 'Another task' }
    useNotificationsStore.getState().setNotifications([n2])
    const { notifications } = useNotificationsStore.getState()
    expect(notifications).toHaveLength(1)
    expect(notifications[0].id).toBe('n-2')
  })

  it('notification has optional action_label and action_url fields', () => {
    const n = {
      ...NOTIFICATION,
      action_label: 'View task',
      action_url: '/tasks/123',
    }
    useNotificationsStore.getState().setNotifications([n])
    const stored = useNotificationsStore.getState().notifications[0]
    expect(stored.action_label).toBe('View task')
    expect(stored.action_url).toBe('/tasks/123')
  })

  it('initial state has empty notifications and ws_connected false', () => {
    const state = useNotificationsStore.getState()
    expect(state.notifications).toHaveLength(0)
    expect(state.ws_connected).toBe(false)
  })
})
