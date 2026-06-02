import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import {
  useTaskFinishedSound,
  playTaskFinishedSound,
} from './useTaskFinishedSound'

// In-memory WebSocket so tests can drive the server side.
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  url: string
  closeCallCount = 0

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closeCallCount += 1
    this.onclose?.({})
  }

  emit(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
}

// Minimal AudioContext stub that records the calls the hook makes.
function makeFakeAudioContext() {
  const stops: number[] = []
  const oscillators: unknown[] = []
  const gains: unknown[] = []
  let closed = false

  class FakeAudioContext {
    currentTime = 0
    destination = {}
    static created = 0

    constructor() {
      FakeAudioContext.created += 1
    }

    createOscillator() {
      const osc = {
        type: '',
        frequency: { value: 0 },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn((t: number) => stops.push(t)),
      }
      oscillators.push(osc)
      return osc
    }

    createGain() {
      const gain = {
        gain: {
          setValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      }
      gains.push(gain)
      return gain
    }

    close() {
      closed = true
      return Promise.resolve()
    }
  }

  return { FakeAudioContext, oscillators, gains, stops, isClosed: () => closed }
}

describe('playTaskFinishedSound', () => {
  it('creates oscillators and a gain envelope on the provided AudioContext', () => {
    const { FakeAudioContext, oscillators, gains } = makeFakeAudioContext()

    playTaskFinishedSound(FakeAudioContext as unknown as typeof AudioContext)

    // Two-note chime => two oscillators + two gains, all started+stopped.
    expect(oscillators).toHaveLength(2)
    expect(gains).toHaveLength(2)
    for (const osc of oscillators as Array<{ start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }>) {
      expect(osc.start).toHaveBeenCalledTimes(1)
      expect(osc.stop).toHaveBeenCalledTimes(1)
    }
    for (const gain of gains as Array<{ gain: { exponentialRampToValueAtTime: ReturnType<typeof vi.fn> } }>) {
      expect(gain.gain.exponentialRampToValueAtTime).toHaveBeenCalled()
    }
  })

  it('is a no-op (no throw) when no AudioContext is available', () => {
    expect(() => playTaskFinishedSound(undefined)).not.toThrow()
  })
})

describe('useTaskFinishedSound', () => {
  let audioCtorSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    // Spy on the global AudioContext so we can assert the sound fired
    // when a needle_closed frame arrives.
    audioCtorSpy = vi.fn().mockImplementation(() => ({
      currentTime: 0,
      destination: {},
      createOscillator: () => ({
        type: '',
        frequency: { value: 0 },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      }),
      createGain: () => ({
        gain: {
          setValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      }),
      close: () => Promise.resolve(),
    }))
    vi.stubGlobal('AudioContext', audioCtorSpy)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('opens a notifications websocket on mount', () => {
    renderHook(() => useTaskFinishedSound())
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toContain('/api/ws/notifications')
  })

  it('plays the sound when a needle_closed event arrives', () => {
    renderHook(() => useTaskFinishedSound())
    const ws = FakeWebSocket.instances[0]
    ws.emit({ type: 'needle_closed', task_id: '→1990' })
    expect(audioCtorSpy).toHaveBeenCalledTimes(1)
  })

  it('ignores unrelated events (snapshot, ping)', () => {
    renderHook(() => useTaskFinishedSound())
    const ws = FakeWebSocket.instances[0]
    ws.emit({ type: 'snapshot', notifications: [] })
    ws.emit({ type: 'ping' })
    expect(audioCtorSpy).not.toHaveBeenCalled()
  })

  it('closes the websocket on unmount', () => {
    const { unmount } = renderHook(() => useTaskFinishedSound())
    const ws = FakeWebSocket.instances[0]
    unmount()
    expect(ws.closeCallCount).toBe(1)
  })
})
