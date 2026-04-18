import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, render, act } from '@testing-library/react'
import { useRef } from 'react'
import { useWebSocket, RECONNECT_MAX_ATTEMPTS } from './useWebSocket'

// A tiny in-memory WebSocket that lets tests drive the server side.
// Captures listeners so the test can fire onopen, onmessage, onclose, etc.
class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  // Existing tests implicitly treat the socket as OPEN on construction.
  // The StrictMode regression test manually flips this to CONNECTING.
  readyState = 1
  sent: string[] = []
  closeCallCount = 0
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.closeCallCount += 1
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

  it('does not emit a second event on close when the server already sent an error', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))
    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => result.current.send({ messages: [] }))
    act(() => ws.emit({ type: 'error', data: 'backend exploded' }))

    // After the server error, lastMessage is the error.
    expect(result.current.lastMessage?.type).toBe('error')
    expect(result.current.lastMessage?.data).toBe('backend exploded')

    act(() => ws.triggerClose())

    // The server already surfaced the error. A subsequent close must
    // not stomp on it with a done or a second error. The lastMessage
    // stays as the server's original error.
    expect(result.current.lastMessage?.type).toBe('error')
    expect(result.current.lastMessage?.data).toBe('backend exploded')
  })

  it('does not emit a second done on close when the server already sent done', () => {
    // Regression: the old code emitted {type:'done'} on EVERY close when
    // streamEndedRef was true. After a real server done + socket close, the
    // ChatPanel would see two done events, potentially re-triggering the
    // grace-window timer and flashing "Done." on a bubble with content.
    const { result } = renderHook(() => useWebSocket('/ws/chat'))
    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => result.current.send({ messages: [] }))
    act(() => ws.emit({ type: 'token', data: 'hello' }))
    act(() => ws.emit({ type: 'done' }))

    // After server done, lastMessage is done.
    expect(result.current.lastMessage?.type).toBe('done')

    // Record the reference so we can detect if it changes.
    const doneRef = result.current.lastMessage

    act(() => ws.triggerClose())

    // lastMessage must NOT have changed. No second done emitted.
    expect(result.current.lastMessage).toBe(doneRef)
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

  // StrictMode double-effect regression. React 19's dev StrictMode
  // runs effects twice: mount, cleanup, mount. If the hook's cleanup
  // calls close() on a socket that is still in CONNECTING state, the
  // browser logs "WebSocket is closed before the connection is
  // established" in the console. That noise clutters dev debugging
  // and can mask real WebSocket errors. The fix is to NOT call
  // close() on a CONNECTING socket, and instead mark it with
  // _shouldCloseOnOpen so its onopen handler closes it cleanly.
  it('does not call close() on a CONNECTING socket during disconnect', () => {
    const { result, unmount } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    // Simulate the real browser state: the handshake is in flight.
    ws.readyState = FakeWebSocket.CONNECTING

    // React StrictMode triggers the cleanup while the socket is still
    // connecting. Unmounting calls disconnect() under the hood.
    unmount()

    // close() must NOT have been called while the socket was CONNECTING.
    expect(ws.closeCallCount).toBe(0)
    // Instead the hook must have stashed a deferred close signal.
    expect(
      (ws as unknown as { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen,
    ).toBe(true)

    // When the handshake finishes, the onopen handler must close the
    // socket cleanly now that we are past CONNECTING.
    ws.readyState = FakeWebSocket.OPEN
    act(() => ws.triggerOpen())
    expect(ws.closeCallCount).toBe(1)
  })

  it('does not call close() on a CONNECTING socket when reconnecting', () => {
    // Same scenario but triggered by a second connect() call rather
    // than unmount. The old socket must be deferred, not slammed.
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws1 = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    ws1.readyState = FakeWebSocket.CONNECTING

    act(() => result.current.connect())
    const ws2 = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]

    expect(ws1).not.toBe(ws2)
    expect(ws1.closeCallCount).toBe(0)
    expect(
      (ws1 as unknown as { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen,
    ).toBe(true)
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

describe('useWebSocket send queue and reconnect backoff', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('queues send() calls made before open and flushes them on onopen', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    // Socket is still CONNECTING (readyState 0).
    ws.readyState = FakeWebSocket.CONNECTING

    // Calling send() before open must NOT throw and must NOT call ws.send() yet.
    act(() => result.current.send({ msg: 'queued' }))
    expect(ws.sent).toHaveLength(0)

    // Once onopen fires the payload must be delivered immediately.
    ws.readyState = FakeWebSocket.OPEN
    act(() => ws.triggerOpen())
    expect(ws.sent).toHaveLength(1)
    expect(JSON.parse(ws.sent[0])).toEqual({ msg: 'queued' })
  })

  it('delivers multiple queued payloads in order on open', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    ws.readyState = FakeWebSocket.CONNECTING

    act(() => {
      result.current.send({ seq: 1 })
      result.current.send({ seq: 2 })
      result.current.send({ seq: 3 })
    })
    expect(ws.sent).toHaveLength(0)

    ws.readyState = FakeWebSocket.OPEN
    act(() => ws.triggerOpen())
    expect(ws.sent).toHaveLength(3)
    expect(JSON.parse(ws.sent[0])).toEqual({ seq: 1 })
    expect(JSON.parse(ws.sent[1])).toEqual({ seq: 2 })
    expect(JSON.parse(ws.sent[2])).toEqual({ seq: 3 })
  })

  it('surfaces a visible error after RECONNECT_MAX_ATTEMPTS consecutive failures', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())

    // Simulate RECONNECT_MAX_ATTEMPTS consecutive onerror events.
    // After each one the hook should schedule a backoff retry; we
    // advance timers to fire it, then let the next attempt fail too.
    for (let i = 0; i < RECONNECT_MAX_ATTEMPTS; i++) {
      const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
      ws.readyState = FakeWebSocket.CONNECTING
      act(() => {
        if (ws.onerror) ws.onerror({})
      })
      // If not the last attempt, advance the timer to trigger reconnect.
      if (i < RECONNECT_MAX_ATTEMPTS - 1) {
        act(() => vi.runAllTimers())
      }
    }

    // After the cap is reached the error message must tell the user to refresh.
    expect(result.current.lastMessage?.type).toBe('error')
    expect(String(result.current.lastMessage?.data || '')).toMatch(/refresh/i)
  })

  it('does NOT schedule another reconnect after RECONNECT_MAX_ATTEMPTS is reached', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const instanceCountBefore = FakeWebSocket.instances.length

    for (let i = 0; i < RECONNECT_MAX_ATTEMPTS; i++) {
      const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
      ws.readyState = FakeWebSocket.CONNECTING
      act(() => {
        if (ws.onerror) ws.onerror({})
      })
      if (i < RECONNECT_MAX_ATTEMPTS - 1) {
        act(() => vi.runAllTimers())
      }
    }

    // Advance timers well past any backoff to confirm no new socket is created.
    act(() => vi.advanceTimersByTime(60000))
    // No extra WebSocket instances beyond the ones already created during attempts.
    expect(FakeWebSocket.instances.length).toBe(instanceCountBefore + RECONNECT_MAX_ATTEMPTS - 1)
  })
})

describe('useWebSocket non-JSON frame handling', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  // Regression for the red "handleMessage" error that fired on page load.
  // The vite WS proxy can forward an HTTP-level error response as plain
  // text ("Upstream unavailable") when the backend is down or restarting.
  // JSON.parse throws on that input, crashing the onmessage callback with
  // an unhandled exception that shows up as a red error in the browser
  // console. The fix wraps JSON.parse in a try/catch and surfaces a
  // user-visible error message instead.
  it('emits an error message instead of throwing when the frame is not JSON', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    // Simulate the proxy sending a plain-text HTTP error frame.
    expect(() => {
      act(() => {
        if (ws.onmessage) ws.onmessage({ data: 'Upstream unavailable' })
      })
    }).not.toThrow()

    // The hook must surface an error event so the chat panel can render it.
    expect(result.current.lastMessage?.type).toBe('error')
    expect(String(result.current.lastMessage?.data || '')).toMatch(/unexpected response/i)
  })

  it('does not throw when the frame is an empty string', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    expect(() => {
      act(() => {
        if (ws.onmessage) ws.onmessage({ data: '' })
      })
    }).not.toThrow()

    expect(result.current.lastMessage?.type).toBe('error')
  })
})

describe('useWebSocket heartbeat frames', () => {
  // The backend sends small {"type": "heartbeat"} frames during long
  // tool-use / thinking phases so the browser and proxies do not close
  // the idle socket. The frontend must drop these frames on the floor:
  // they must not become lastMessage (or the consumer effect would
  // re-run for every beat), and they must not flip the stream-ended
  // tracking flag either way.
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  it('ignores heartbeat frames without updating lastMessage', () => {
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    // First real event establishes a baseline lastMessage.
    act(() => ws.emit({ type: 'token', data: 'streaming...' }))
    expect(result.current.lastMessage?.type).toBe('token')

    // Heartbeat arrives. It must NOT overwrite the last real event.
    act(() => ws.emit({ type: 'heartbeat' }))
    expect(result.current.lastMessage?.type).toBe('token')
    expect(result.current.lastMessage?.data).toBe('streaming...')
  })

  it('does not mark the stream as ended when a heartbeat arrives mid-turn', () => {
    // Regression guard. If the hook treated heartbeat as a terminal
    // event, a subsequent socket close would look like a normal end of
    // turn instead of a mid-turn drop, and the error message would
    // disappear. This test sends a heartbeat during an active turn,
    // then drops the socket, and expects the error path to fire.
    const { result } = renderHook(() => useWebSocket('/ws/chat'))

    act(() => result.current.connect())
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => ws.triggerOpen())

    act(() => result.current.send({ messages: [{ role: 'user', content: 'anything' }] }))
    act(() => ws.emit({ type: 'token', data: 'starting...' }))
    // Long stall window: server fires a heartbeat to keep the socket alive.
    act(() => ws.emit({ type: 'heartbeat' }))
    act(() => ws.emit({ type: 'heartbeat' }))
    // Now the socket actually drops before the turn finished.
    act(() => ws.triggerClose())

    expect(result.current.lastMessage?.type).toBe('error')
    expect(String(result.current.lastMessage?.data || '')).toMatch(/connection dropped/i)
  })
})
