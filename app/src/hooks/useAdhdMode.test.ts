import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAdhdMode } from './useAdhdMode'

describe('useAdhdMode polling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()

    fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/adhd/config') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ enabled: false, check_in_seconds: 30, focus_mode: false }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ running_count: 0, agents: [], timestamp: '' }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does not poll check-in when ADHD mode is disabled', async () => {
    renderHook(() => useAdhdMode())

    await act(async () => {})
    const checkInCalls = fetchMock.mock.calls.filter(([url]: [string]) => url === '/api/adhd/check-in')
    expect(checkInCalls.length).toBe(0)

    act(() => { vi.advanceTimersByTime(60_000) })
    await act(async () => {})
    const checkInCalls2 = fetchMock.mock.calls.filter(([url]: [string]) => url === '/api/adhd/check-in')
    expect(checkInCalls2.length).toBe(0)
  })

  it('aborts in-flight check-in fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((url: string, opts: RequestInit) => {
      if (url === '/api/adhd/config') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ enabled: true, check_in_seconds: 30, focus_mode: false }),
        })
      }
      if (url === '/api/adhd/check-in') {
        capturedSignal = opts?.signal as AbortSignal
        return new Promise(() => {}) // never resolves
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })

    const { unmount } = renderHook(() => useAdhdMode())
    // Config loads → enabled=true → first tick fires
    await act(async () => {})

    expect(capturedSignal).toBeDefined()
    expect(capturedSignal?.aborted).toBe(false)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('doubles backoff after check-in fetch errors', async () => {
    let configLoaded = false
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/adhd/config') {
        configLoaded = true
        return Promise.resolve({
          ok: true,
          json: async () => ({ enabled: true, check_in_seconds: 10, focus_mode: false }),
        })
      }
      if (url === '/api/adhd/check-in') {
        return Promise.reject(new Error('network error'))
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })

    renderHook(() => useAdhdMode())

    // Config loads first
    await act(async () => {})
    expect(configLoaded).toBe(true)

    // tick 1 fires (enabled=true triggers tick), fails → backoff 1000
    await act(async () => {})
    const calls1 = fetchMock.mock.calls.filter(([u]: [string]) => u === '/api/adhd/check-in').length
    expect(calls1).toBe(1)

    // tick 2 at 1000ms
    act(() => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    const calls2 = fetchMock.mock.calls.filter(([u]: [string]) => u === '/api/adhd/check-in').length
    expect(calls2).toBe(2)

    // tick 3 at 2000ms (doubled)
    act(() => { vi.advanceTimersByTime(2000) })
    await act(async () => {})
    const calls3 = fetchMock.mock.calls.filter(([u]: [string]) => u === '/api/adhd/check-in').length
    expect(calls3).toBe(3)
  })

  it('does not pile up check-in requests when fetch is slow', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/adhd/config') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ enabled: true, check_in_seconds: 5, focus_mode: false }),
        })
      }
      if (url === '/api/adhd/check-in') {
        return new Promise(() => {}) // never resolves
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })

    renderHook(() => useAdhdMode())
    await act(async () => {}) // config loads, tick fires

    // Advance well past multiple check-in intervals
    act(() => { vi.advanceTimersByTime(60_000) })
    await act(async () => {})

    const checkInCalls = fetchMock.mock.calls.filter(([u]: [string]) => u === '/api/adhd/check-in').length
    // Only 1 in-flight — no pile-up
    expect(checkInCalls).toBe(1)
  })
})
