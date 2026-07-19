// →2982 visibility guard for the notifications HTTP fallback poll.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useNotificationsFeed } from './useNotificationsFeed'
import { useNotificationsStore } from '../stores/notificationsStore'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() { this.onclose?.({}) }
  triggerOpen() { this.onopen?.({}) }
  triggerClose() { this.onclose?.({}) }
}

function setTabHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('useNotificationsFeed visibility guard (→2982)', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ([]),
    })
    vi.stubGlobal('fetch', fetchMock)
    useNotificationsStore.setState({ notifications: [], wsConnected: false, snapshotReceived: false })
  })

  afterEach(() => {
    Reflect.deleteProperty(document, 'hidden')
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('pauses polling while the tab is hidden', async () => {
    renderHook(() => useNotificationsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    act(() => setTabHidden(true))
    act(() => { vi.advanceTimersByTime(30_000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('resumes polling when the tab becomes visible again', async () => {
    renderHook(() => useNotificationsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    act(() => setTabHidden(true))
    act(() => { vi.advanceTimersByTime(30_000) })
    await act(async () => {})

    act(() => setTabHidden(false))
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not begin polling on WS close while hidden, but does once visible', async () => {
    renderHook(() => useNotificationsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => setTabHidden(true))
    act(() => ws.triggerClose())
    await act(async () => {})
    expect(fetchMock).not.toHaveBeenCalled()

    act(() => setTabHidden(false))
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
