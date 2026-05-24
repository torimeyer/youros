import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useDashboardFeed } from './useDashboardFeed'
import { useDashboardStore } from '../stores/dashboardStore'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  closeCallCount = 0
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closeCallCount += 1
    if (this.onclose) this.onclose({})
  }

  triggerOpen() { this.onopen?.({}) }
  triggerClose() { this.onclose?.({}) }
}

describe('useDashboardFeed polling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()

    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: { open: 3 } }),
    })
    vi.stubGlobal('fetch', fetchMock)

    useDashboardStore.setState({ agentsCount: 0, tasksCount: 0, wsConnected: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('connects to the /api-prefixed websocket path', () => {
    renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    expect(ws.url).toContain('/api/ws/dashboard/data')
  })

  it('does not poll while WebSocket is open', async () => {
    const { unmount } = renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    await act(async () => { vi.advanceTimersByTime(15_000) })
    expect(fetchMock).not.toHaveBeenCalled()
    unmount()
  })

  it('starts polling when WebSocket closes', async () => {
    renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not stack multiple pollers on repeated WS closes', async () => {
    renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => ws.triggerClose())
    await act(async () => {})

    act(() => vi.advanceTimersByTime(5000))
    await act(async () => {})

    const callsBefore = fetchMock.mock.calls.length

    act(() => vi.advanceTimersByTime(5000))
    await act(async () => {})
    const callsAfter = fetchMock.mock.calls.length

    expect(callsAfter - callsBefore).toBeLessThanOrEqual(1)
  })

  it('stops polling when WebSocket reconnects successfully', async () => {
    renderHook(() => useDashboardFeed())
    const ws1 = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws1.triggerOpen())
    act(() => ws1.triggerClose())
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    act(() => vi.advanceTimersByTime(5000))
    await act(async () => {})
    const ws2 = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws2.triggerOpen())

    const callsAtReconnect = fetchMock.mock.calls.length

    act(() => vi.advanceTimersByTime(15_000))
    await act(async () => {})
    expect(fetchMock.mock.calls.length).toBe(callsAtReconnect)
  })

  it('aborts in-flight fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts.signal as AbortSignal
      return new Promise(() => {}) // never resolves
    })

    const { unmount } = renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())
    await act(async () => {})

    expect(capturedSignal?.aborted).toBe(false)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('doubles backoff after fetch error, up to 60s', async () => {
    fetchMock.mockRejectedValue(new Error('network error'))

    renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // First tick fires immediately, fails → backoff = 1000
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Next tick scheduled at 1000ms
    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Next tick at 2000ms (doubled)
    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(3)

    // Next tick at 4000ms (doubled again)
    act(() => { vi.advanceTimersByTime(4000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('resets backoff to 0 after a successful fetch', async () => {
    let callCount = 0
    fetchMock.mockImplementation(() => {
      callCount++
      if (callCount <= 2) return Promise.reject(new Error('fail'))
      return Promise.resolve({ ok: true, json: async () => ({ counts: { open: 1 } }) })
    })

    renderHook(() => useDashboardFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // tick 1 fails → backoff 1000
    await act(async () => {})
    // tick 2 fails → backoff 2000
    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    // tick 3 succeeds → backoff reset to 0
    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})

    const callsAfterSuccess = fetchMock.mock.calls.length

    // Next tick should be at POLL_MS (5000), not 4000 (next backoff step)
    act(() => { vi.advanceTimersByTime(5000) })
    await act(async () => {})
    expect(fetchMock.mock.calls.length).toBe(callsAfterSuccess + 1)
  })
})
