import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

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
  triggerMessage(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
}

describe('useVictoryFanfare', () => {
  let mockPlay: ReturnType<typeof vi.fn>

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockPlay = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('plays fanfare when needle_closed event arrives', async () => {
    const { useVictoryFanfare } = await import('./useVictoryFanfare')
    renderHook(() => useVictoryFanfare(mockPlay))
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    expect(ws.url).toContain('/ws/notifications')

    await act(async () => {
      ws.triggerMessage({ type: 'needle_closed', task_id: '→1234' })
    })

    expect(mockPlay).toHaveBeenCalledTimes(1)
  })

  it('does not play for ping messages', async () => {
    const { useVictoryFanfare } = await import('./useVictoryFanfare')
    renderHook(() => useVictoryFanfare(mockPlay))
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]

    await act(async () => {
      ws.triggerMessage({ type: 'ping' })
    })

    expect(mockPlay).not.toHaveBeenCalled()
  })

  it('does not play for snapshot messages', async () => {
    const { useVictoryFanfare } = await import('./useVictoryFanfare')
    renderHook(() => useVictoryFanfare(mockPlay))
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]

    await act(async () => {
      ws.triggerMessage({ type: 'snapshot', notifications: [] })
    })

    expect(mockPlay).not.toHaveBeenCalled()
  })

  it('reconnects after disconnect', async () => {
    vi.useFakeTimers()
    const { useVictoryFanfare } = await import('./useVictoryFanfare')
    renderHook(() => useVictoryFanfare(mockPlay))

    const ws = FakeWebSocket.instances[0]
    ws.close()

    act(() => { vi.advanceTimersByTime(3100) })
    expect(FakeWebSocket.instances.length).toBe(2)

    vi.useRealTimers()
  })
})
