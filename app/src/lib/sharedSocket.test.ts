import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  subscribeSharedSocket,
  _activeChannelCount,
  _resetSharedSockets,
} from './sharedSocket'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static OPEN = 1
  static CONNECTING = 0
  static CLOSING = 2
  static CLOSED = 3
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  readyState = FakeWebSocket.CONNECTING
  closeCallCount = 0
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closeCallCount += 1
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({})
  }

  triggerOpen() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({})
  }
  triggerClose() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({})
  }
  triggerMessage(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }

  // Only sockets actually attached to a channel count as "live" for the
  // duplicate-socket assertion: a socket we deliberately closed is not.
  get isLive() {
    return this.readyState === FakeWebSocket.OPEN || this.readyState === FakeWebSocket.CONNECTING
  }
}

function liveCount() {
  return FakeWebSocket.instances.filter((w) => w.isLive).length
}

describe('sharedSocket — one socket per channel', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    _resetSharedSockets()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('opens exactly one socket for the first subscriber', () => {
    subscribeSharedSocket('/api/ws/notifications', {})
    expect(FakeWebSocket.instances.length).toBe(1)
    expect(_activeChannelCount()).toBe(1)
  })

  it('shares a single socket across many subscribers on the same path', () => {
    const a = subscribeSharedSocket('/api/ws/notifications', {})
    const b = subscribeSharedSocket('/api/ws/notifications', {})
    const c = subscribeSharedSocket('/api/ws/notifications', {})
    // Three listeners, but still ONE socket and ONE channel.
    expect(FakeWebSocket.instances.length).toBe(1)
    expect(liveCount()).toBe(1)
    expect(_activeChannelCount()).toBe(1)
    a()
    b()
    c()
  })

  it('keeps the socket open until the LAST subscriber unsubscribes', () => {
    const a = subscribeSharedSocket('/api/ws/notifications', {})
    const b = subscribeSharedSocket('/api/ws/notifications', {})
    const ws = FakeWebSocket.instances[0]
    ws.triggerOpen()

    a() // first leaves — socket must stay open
    expect(ws.closeCallCount).toBe(0)
    expect(liveCount()).toBe(1)

    b() // last leaves — socket closes now
    expect(ws.closeCallCount).toBe(1)
    expect(liveCount()).toBe(0)
    expect(_activeChannelCount()).toBe(0)
  })

  it('opens separate sockets for separate channels', () => {
    subscribeSharedSocket('/api/ws/notifications', {})
    subscribeSharedSocket('/api/ws/dashboard/data', {})
    subscribeSharedSocket('/api/ws/calendar/events', {})
    expect(_activeChannelCount()).toBe(3)
    expect(liveCount()).toBe(3)
  })

  it('delivers messages to every subscriber on the channel', () => {
    const seenA: unknown[] = []
    const seenB: unknown[] = []
    subscribeSharedSocket('/api/ws/notifications', { onMessage: (d) => seenA.push(d) })
    subscribeSharedSocket('/api/ws/notifications', { onMessage: (d) => seenB.push(d) })
    const ws = FakeWebSocket.instances[0]
    ws.triggerOpen()
    ws.triggerMessage({ type: 'snapshot', notifications: [] })
    expect(seenA).toHaveLength(1)
    expect(seenB).toHaveLength(1)
  })

  it('reconnects after a drop without leaking a second live socket', () => {
    subscribeSharedSocket('/api/ws/dashboard/data', {})
    const ws1 = FakeWebSocket.instances[0]
    ws1.triggerOpen()
    ws1.triggerClose() // server drops the socket

    // Exactly one reconnect fires after the delay, producing one new socket.
    vi.advanceTimersByTime(5000)
    expect(FakeWebSocket.instances.length).toBe(2)
    expect(liveCount()).toBe(1) // old one is closed, only the new one is live
  })

  it('does not reconnect once the last subscriber has left', () => {
    const off = subscribeSharedSocket('/api/ws/dashboard/data', {})
    const ws = FakeWebSocket.instances[0]
    ws.triggerOpen()
    off() // teardown
    expect(_activeChannelCount()).toBe(0)

    // Even if timers run, no new socket should appear.
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances.length).toBe(1)
    expect(liveCount()).toBe(0)
  })

  it('survives a simulated React StrictMode double subscribe/unsubscribe with one socket', () => {
    // StrictMode dev double-invoke pattern: subscribe, unsubscribe, subscribe.
    const off1 = subscribeSharedSocket('/api/ws/calendar/events', {})
    off1()
    const off2 = subscribeSharedSocket('/api/ws/calendar/events', {})
    // After the dust settles there is one channel and one live socket.
    expect(_activeChannelCount()).toBe(1)
    expect(liveCount()).toBe(1)
    off2()
    expect(_activeChannelCount()).toBe(0)
    expect(liveCount()).toBe(0)
  })
})
