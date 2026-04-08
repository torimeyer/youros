import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWebSocket } from './useWebSocket'

// A tiny in-memory WebSocket that lets tests drive the server side.
// Captures listeners so the test can fire onopen, onmessage, onclose, etc.
class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []

  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  readyState = 1
  sent: string[] = []
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    if (this.onclose) this.onclose({})
  }

  // Test helpers.
  emit(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  triggerOpen() {
    this.onopen?.({})
  }
  triggerClose() {
    this.onclose?.({})
  }
}

describe('useWebSocket stream close handling', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  it('emits a silent done on close when the server already finished the turn', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    // Simulate a normal turn: send a message, receive token + done.
    act(() => result.current.send({ messages: [] }))
    act(() => ws.emit({ type: 'token', data: 'hello' }))
    act(() => ws.emit({ type: 'done', usage: { input_tokens: 1, output_tokens: 1 } }))
    // Now the server closes the socket (e.g. reload). Because the turn
    // already finished, the close should be silent and the last message
    // should remain the done event (re-emitted silently is fine).
    act(() => ws.triggerClose())

    // After a normal close, no error should be surfaced.
    expect(result.current.lastMessage?.type).toBe('done')
  })

  it('emits an error on close when the turn was still in progress', () => {
    // This is the regression guard for the silent empty-bubble bug.
    // When the WebSocket drops mid-turn (for example, uvicorn --reload
    // restarts the server because a background agent edited a file), the
    // chat panel MUST show a visible error instead of silently clearing
    // the assistant bubble and leaving Tori staring at an empty row.
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    // User sends a message. We have NOT yet received any token or done.
    act(() => result.current.send({ messages: [{ role: 'user', content: 'did you complete it?' }] }))
    // Server dies before emitting anything (or after model_label only).
    act(() => ws.emit({ type: 'model_label', data: 'Claude' }))
    act(() => ws.triggerClose())

    // An error must be surfaced so the chat panel can render it and clear
    // the empty placeholder with a real message.
    expect(result.current.lastMessage?.type).toBe('error')
    expect(String(result.current.lastMessage?.data || '')).toMatch(/try again/i)
  })

  it('treats a close after thinking-only but before any token as an error', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))
    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => result.current.send({ messages: [] }))
    act(() => ws.emit({ type: 'thinking', data: true }))
    act(() => ws.triggerClose())

    expect(result.current.lastMessage?.type).toBe('error')
  })

  it('clears the in-progress flag when the server sends an explicit error', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))
    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => result.current.send({ messages: [] }))
    act(() => ws.emit({ type: 'error', data: 'backend exploded' }))
    act(() => ws.triggerClose())

    // The server already surfaced the error. A subsequent close should
    // not stomp on it with a second error.
    expect(result.current.lastMessage?.type).not.toBe('error')
  })
})
