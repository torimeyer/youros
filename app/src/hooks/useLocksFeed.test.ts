import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useLocksFeed } from './useLocksFeed'
import { useLocksStore } from '../stores/locksStore'

// Minimal WebSocket fake — exposes trigger helpers for open/close.
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

describe('useLocksFeed fallback polling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()

    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ locks: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    useLocksStore.setState({ locks: [], wsConnected: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does not poll while WebSocket is open', async () => {
    const { unmount } = renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    await act(async () => { vi.advanceTimersByTime(20_000) })
    expect(fetchMock).not.toHaveBeenCalled()
    unmount()
  })

  it('starts polling after WS closes (after fallback delay)', async () => {
    renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // Before fallback delay (3s) nothing has been fetched
    act(() => { vi.advanceTimersByTime(2999) })
    await act(async () => {})
    expect(fetchMock).not.toHaveBeenCalled()

    // After fallback delay the first tick fires
    act(() => { vi.advanceTimersByTime(1) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('aborts in-flight fetch when WebSocket reconnects', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts?.signal as AbortSignal
      return new Promise(() => {}) // never resolves
    })

    renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // Advance past fallback delay to start first tick
    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})

    expect(capturedSignal?.aborted).toBe(false)
    // WS reconnects — should abort the in-flight fetch
    act(() => ws.triggerOpen())
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('aborts in-flight fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts?.signal as AbortSignal
      return new Promise(() => {}) // never resolves
    })

    const { unmount } = renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})

    expect(capturedSignal?.aborted).toBe(false)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('doubles backoff after fetch errors, up to 30s', async () => {
    fetchMock.mockRejectedValue(new Error('network error'))

    renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // Past fallback delay — first tick fires, fails → backoff = 1000
    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Next tick at 1000ms
    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Next tick at 2000ms (doubled)
    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('does not pile up requests when fetch is slow (chained setTimeout, not setInterval)', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {})) // never resolves

    renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})

    // Advance well past multiple poll intervals
    act(() => { vi.advanceTimersByTime(50_000) })
    await act(async () => {})

    // Only 1 in-flight request — no pile-up
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('resets backoff after a successful fetch', async () => {
    let callCount = 0
    fetchMock.mockImplementation(() => {
      callCount++
      if (callCount <= 2) return Promise.reject(new Error('fail'))
      return Promise.resolve({ ok: true, json: async () => ({ locks: [] }) })
    })

    renderHook(() => useLocksFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    // Kick off fallback, tick 1 fails → backoff 1000
    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})
    // tick 2 fails → backoff 2000
    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    // tick 3 succeeds → backoff reset
    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})

    const callsAfterSuccess = fetchMock.mock.calls.length

    // Next tick should be at FALLBACK_POLL_MS (10000), not 4000
    act(() => { vi.advanceTimersByTime(10_000) })
    await act(async () => {})
    expect(fetchMock.mock.calls.length).toBe(callsAfterSuccess + 1)
  })
})
