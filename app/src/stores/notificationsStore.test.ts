import { describe, it, expect, beforeEach } from 'vitest'
import { useNotificationsStore } from './notificationsStore'

const NOTIF = { id: 'n-1', type: 'info', title: 'Hi', body: 'there' }

describe('useNotificationsStore', () => {
  beforeEach(() => {
    useNotificationsStore.setState({
      notifications: [],
      wsConnected: false,
      snapshotReceived: false,
    })
  })

  it('setNotifications replaces the list', () => {
    useNotificationsStore.getState().setNotifications([NOTIF])
    expect(useNotificationsStore.getState().notifications).toHaveLength(1)
    expect(useNotificationsStore.getState().notifications[0].id).toBe('n-1')
  })

  it('content-equal setNotifications keeps the same reference (→1730)', () => {
    useNotificationsStore.getState().setNotifications([NOTIF])
    const first = useNotificationsStore.getState().notifications
    useNotificationsStore.getState().setNotifications([{ ...NOTIF }])
    expect(useNotificationsStore.getState().notifications).toBe(first)
  })

  it('a real change swaps the reference', () => {
    useNotificationsStore.getState().setNotifications([NOTIF])
    const first = useNotificationsStore.getState().notifications
    useNotificationsStore.getState().setNotifications([{ ...NOTIF, read: true }])
    expect(useNotificationsStore.getState().notifications).not.toBe(first)
  })
})
