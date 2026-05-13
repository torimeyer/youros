import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSessionsFeed } from './useSessionsFeed'
import { useSessionsStore } from '../stores/sessionsStore'

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

describe('useSessionsFeed fallback polling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()

    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ sessions: [], locks: [], events: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    useSessionsStore.setState({ sessions: [], locks: [], events: [], isConnected: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does not poll while WebSocket is open', async () => {
    const { unmount } = renderHook(() => useSessionsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    await act(async () => { vi.advanceTimersByTime(20_000) })
    expect(fetchMock).not.toHaveBeenCalled()
    unmount()
  })

  it('aborts in-flight fetch when WebSocket reconnects', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts?.signal as AbortSignal
      return new Promise(() => {})
    })

    renderHook(() => useSessionsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})

    expect(capturedSignal?.aborted).toBe(false)
    act(() => ws.triggerOpen())
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('aborts in-flight fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts?.signal as AbortSignal
      return new Promise(() => {})
    })

    const { unmount } = renderHook(() => useSessionsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})

    expect(capturedSignal?.aborted).toBe(false)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('doubles backoff after fetch errors', async () => {
    fetchMock.mockRejectedValue(new Error('network error'))

    renderHook(() => useSessionsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(2)

    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('does not pile up requests when fetch is slow', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}))

    renderHook(() => useSessionsFeed())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())
    act(() => ws.triggerClose())

    act(() => { vi.advanceTimersByTime(3001) })
    await act(async () => {})
    act(() => { vi.advanceTimersByTime(50_000) })
    await act(async () => {})

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
