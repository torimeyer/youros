import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, render, act } from '@testing-library/react'
import { useRef } from 'react'
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

describe('useWebSocket multi-event burst handling', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  // Regression for the wall-of-text bug. When the backend sends a burst
  // of events in quick succession (multi_ai chat sends ~39 events for a
  // 6-turn exchange), React used to batch the setLastMessage calls and
  // drop every event except the last one in each batch. The consumer
  // effect only saw the LAST event of every batch, so multi_ai_turn_start
  // events were lost and every token landed in the same bubble.
  //
  // The fix is flushSync inside ws.onmessage so each setLastMessage commits
  // synchronously and the consumer effect runs once per message in order.
  // This test drives a real burst through the real hook and counts how
  // many distinct lastMessage values the consumer observes.
  it('delivers every event in a burst, not just the last one', () => {
    const seenTypes: string[] = []
    const seenData: unknown[] = []

    function Consumer() {
      const { lastMessage, connect } = useWebSocket('/ws/chat')
      // Run connect on first render so the test does not have to.
      const didConnectRef = useRef(false)
      if (!didConnectRef.current) {
        didConnectRef.current = true
        connect()
      }
      // Record every distinct lastMessage we observe. Without flushSync
      // this list would only contain the last message of each batch.
      if (lastMessage) {
        seenTypes.push(lastMessage.type)
        seenData.push(lastMessage.data ?? null)
      }
      return null
    }

    render(<Consumer />)
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    // Drive a burst that mirrors a real multi_ai chat round one frame.
    // 12 frames in a single act() block. Without flushSync only the last
    // one would survive. With flushSync all 12 must show up in seenTypes.
    act(() => {
      ws.emit({ type: 'multi_ai_status', data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 } })
      ws.emit({ type: 'multi_ai_status', data: { phase: 'thinking', model: 'gemini', round: 1 } })
      ws.emit({ type: 'multi_ai_turn_start', data: { model: 'gemini', round: 1 } })
      ws.emit({ type: 'multi_ai_status', data: { phase: 'speaking', model: 'gemini', round: 1 } })
      ws.emit({ type: 'token', data: 'hello ' })
      ws.emit({ type: 'token', data: 'claude ' })
      ws.emit({ type: 'multi_ai_turn_end', data: { model: 'gemini', round: 1 } })
      ws.emit({ type: 'multi_ai_status', data: { phase: 'thinking', model: 'claude', round: 1 } })
      ws.emit({ type: 'multi_ai_turn_start', data: { model: 'claude', round: 1 } })
      ws.emit({ type: 'multi_ai_status', data: { phase: 'speaking', model: 'claude', round: 1 } })
      ws.emit({ type: 'token', data: 'hi gemini' })
      ws.emit({ type: 'multi_ai_turn_end', data: { model: 'claude', round: 1 } })
    })

    // Every single emitted message must have been observed by the consumer.
    // Before the flushSync fix, this list would have ~1 entry instead of 12.
    expect(seenTypes).toEqual([
      'multi_ai_status',
      'multi_ai_status',
      'multi_ai_turn_start',
      'multi_ai_status',
      'token',
      'token',
      'multi_ai_turn_end',
      'multi_ai_status',
      'multi_ai_turn_start',
      'multi_ai_status',
      'token',
      'multi_ai_turn_end',
    ])
    // Both token strings must be present in order, not just the last one.
    expect(seenData.filter((d) => typeof d === 'string')).toEqual(['hello ', 'claude ', 'hi gemini'])
  })

  it('preserves order across two consecutive bursts', () => {
    const seenTypes: string[] = []

    function Consumer() {
      const { lastMessage, connect } = useWebSocket('/ws/chat')
      const didConnectRef = useRef(false)
      if (!didConnectRef.current) {
        didConnectRef.current = true
        connect()
      }
      if (lastMessage) {
        seenTypes.push(lastMessage.type)
      }
      return null
    }

    render(<Consumer />)
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => {
      ws.emit({ type: 'token', data: 'a' })
      ws.emit({ type: 'token', data: 'b' })
    })
    act(() => {
      ws.emit({ type: 'token', data: 'c' })
      ws.emit({ type: 'done' })
    })

    expect(seenTypes).toEqual(['token', 'token', 'token', 'done'])
  })
})
