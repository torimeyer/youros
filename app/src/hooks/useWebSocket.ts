import { useEffect, useState, useCallback, useRef } from 'react'
import { flushSync } from 'react-dom'

interface WSMessage {
  type: string
  data?: string | Record<string, unknown>
  usage?: { input_tokens: number; output_tokens: number }
  return_code?: number
}

// Reconnect backoff: starts at 1 s, doubles each attempt, caps at 16 s.
const RECONNECT_BASE_MS = 1000
const RECONNECT_CAP_MS = 16000
// After this many consecutive failed connects, stop retrying and surface
// a visible error so the user is not left staring at a frozen chat panel.
export const RECONNECT_MAX_ATTEMPTS = 10

export function useWebSocket(path: string, autoConnect = false) {
  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const pathRef = useRef(path)
  const gotMessageRef = useRef(false)
  const hadErrorRef = useRef(false)
  // Tracks whether the server already sent a proper done/error for the
  // current turn. If the socket closes before that, we treat the close as
  // an unexpected drop and surface an error instead of a silent done.
  const streamEndedRef = useRef(true)
  // Tracks whether a server-sent done or error was already received for
  // the current turn. Used by onclose to avoid emitting a SECOND done
  // event that can trigger the grace-window timer again and produce a
  // flash of "Done." or a stale confirmedDoneIds entry.
  const serverDoneReceivedRef = useRef(false)
  // Queue of payloads the caller tried to send before the socket finished
  // opening. Flushed in order from the onopen handler so nothing is ever
  // silently dropped on the very first send after connect().
  const pendingSendsRef = useRef<string[]>([])
  // Reconnect state: consecutive failure count and the current backoff timer.
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  pathRef.current = path

  // Clear any pending reconnect timer.
  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    // Close existing connection if any. Never call close() on a socket
    // that is still in CONNECTING state: the browser logs
    // "WebSocket is closed before the connection is established"
    // which clutters the dev console and can mask real errors. Instead
    // mark the stale socket with _shouldCloseOnOpen so its onopen
    // handler closes it cleanly after the handshake finishes. This is
    // the exact path React 19's StrictMode dev double-effect hits.
    if (wsRef.current) {
      const prev = wsRef.current
      if (prev.readyState === WebSocket.OPEN) {
        prev.close()
      } else if (prev.readyState === WebSocket.CONNECTING) {
        ;(prev as WebSocket & { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen = true
      }
      wsRef.current = null
    }
    clearReconnectTimer()

    gotMessageRef.current = false
    hadErrorRef.current = false
    streamEndedRef.current = true
    serverDoneReceivedRef.current = false

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}${pathRef.current}`)
    ws.onopen = () => {
      if ((ws as WebSocket & { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen) {
        ws.close()
        return
      }
      // Successful connect: reset the failure counter.
      reconnectAttemptsRef.current = 0
      setIsConnected(true)
      // Flush any sends that were queued while the socket was still
      // connecting. Mark the stream as in progress for each so a mid-turn
      // drop becomes a real error instead of a silent done.
      if (pendingSendsRef.current.length > 0) {
        const queued = pendingSendsRef.current
        pendingSendsRef.current = []
        for (const payload of queued) {
          streamEndedRef.current = false
          serverDoneReceivedRef.current = false
          ws.send(payload)
        }
      }
    }
    ws.onclose = () => {
      setIsConnected(false)
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      // Three cases:
      // 1. Server already sent done/error → close is normal cleanup. Do NOT
      //    emit a second done (it re-triggers the grace-window timer and can
      //    flash "Done." or leave stale confirmedDoneIds entries).
      // 2. Stream was in progress (tokens started, no done yet) → socket
      //    dropped mid-turn. Surface an error so the bubble never stays blank.
      // 3. No messages at all and stream never started → idle close after a
      //    connect-only cycle (chat panel opened then closed). Harmless done
      //    so any leftover streaming state gets cleared.
      if (serverDoneReceivedRef.current) {
        // Case 1: server handled the turn, do nothing.
      } else if (!streamEndedRef.current) {
        // Case 2: mid-stream drop.
        streamEndedRef.current = true
        flushSync(() => {
          setLastMessage({
            type: 'error',
            data: 'Connection dropped before the response finished. Please try again.',
          })
        })
      } else {
        // Case 3: idle close, no server events received at all.
        flushSync(() => {
          setLastMessage({ type: 'done' })
        })
      }
    }
    ws.onerror = () => {
      hadErrorRef.current = true
      streamEndedRef.current = true
      // Count this as a failed connect attempt.
      reconnectAttemptsRef.current += 1
      if (reconnectAttemptsRef.current >= RECONNECT_MAX_ATTEMPTS) {
        // Cap reached: stop retrying and surface a visible error.
        flushSync(() => {
          setLastMessage({
            type: 'error',
            data: 'Unable to connect to the server after several attempts. Refresh the page to try again.',
          })
        })
        return
      }
      // Exponential backoff: 1 s, 2 s, 4 s, 8 s, 16 s (capped).
      const delay = Math.min(
        RECONNECT_BASE_MS * Math.pow(2, reconnectAttemptsRef.current - 1),
        RECONNECT_CAP_MS,
      )
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null
        connect()
      }, delay)
      flushSync(() => {
        setLastMessage({ type: 'error', data: 'Connection error. Please try again.' })
      })
    }
    ws.onmessage = (event) => {
      let parsed: WSMessage
      try {
        parsed = JSON.parse(event.data) as WSMessage
      } catch {
        gotMessageRef.current = true
        // The WS proxy (or the backend) sent a non-JSON frame.  This can
        // happen when the vite proxy forwards an HTTP-level error as plain
        // text instead of closing the socket cleanly.  Treat it as an
        // error so the chat panel shows a user-visible message instead of
        // crashing the handleMessage callback with an unhandled exception.
        streamEndedRef.current = true
        flushSync(() => {
          setLastMessage({ type: 'error', data: 'Unexpected response from server. Please try again.' })
        })
        return
      }
      // Heartbeat frames exist only to keep the socket warm during long
      // silent phases (extended thinking, tool-use planning). They
      // carry no user-facing content, must never update lastMessage (or
      // the consumer effect would re-run once per beat), and must not
      // flip streamEndedRef either way since they convey nothing about
      // turn state.
      if (parsed.type === 'heartbeat') {
        return
      }
      gotMessageRef.current = true
      // Mark the stream as in-progress on any event that signals active
      // work so a mid-turn socket close becomes an error instead of a
      // silent done. tool_use and tool_result are included because a
      // turn that consists solely of tool calls (no text tokens) must
      // still surface a connection drop as an error.
      if (
        parsed.type === 'token' ||
        parsed.type === 'thinking' ||
        parsed.type === 'tool_use' ||
        parsed.type === 'tool_use_delta' ||
        parsed.type === 'mcp_tool_use' ||
        parsed.type === 'tool_result' ||
        parsed.type === 'mcp_tool_result'
      ) {
        streamEndedRef.current = false
      } else if (parsed.type === 'done' || parsed.type === 'error') {
        streamEndedRef.current = true
        serverDoneReceivedRef.current = true
      }
      // CRITICAL: flushSync forces React to commit this state update
      // synchronously instead of batching it with other onmessage events
      // that may arrive in quick succession. Without this, multi-AI chat
      // bursts (turn_start, token, turn_end, repeat) get coalesced into
      // a single re-render with only the LAST event surviving, so the
      // consumer effect drops every intermediate event and crams 6 turns
      // into one bubble. flushSync makes the consumer effect run once
      // per message, in order, the way the streaming protocol requires.
      flushSync(() => {
        setLastMessage(parsed)
      })
    }
    wsRef.current = ws
  }, [clearReconnectTimer])

  const disconnect = useCallback(() => {
    clearReconnectTimer()
    reconnectAttemptsRef.current = 0
    const ws = wsRef.current
    if (ws) {
      // Same rule as connect(): do not call close() on a CONNECTING
      // socket. Mark it so onopen closes cleanly instead. This kills
      // the "closed before established" warning that React 19
      // StrictMode's double-effect used to fire on every mount.
      if (ws.readyState === WebSocket.OPEN) {
        ws.close()
      } else if (ws.readyState === WebSocket.CONNECTING) {
        ;(ws as WebSocket & { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen = true
      }
    }
    wsRef.current = null
  }, [clearReconnectTimer])

  const send = useCallback((data: unknown) => {
    const payload = JSON.stringify(data)
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      // Mark the stream as in progress so a mid-turn close becomes an error
      // instead of a silent done. The server will flip this back to true
      // when it sends a real done or error event.
      streamEndedRef.current = false
      serverDoneReceivedRef.current = false
      ws.send(payload)
      return
    }
    if (ws?.readyState === WebSocket.CONNECTING) {
      // Socket is still handshaking. Queue the payload so the onopen
      // handler flushes it as soon as the connection is live. Without this
      // queue the UI would set isStreaming=true and spin forever on the
      // first send after opening the chat panel.
      pendingSendsRef.current.push(payload)
      return
    }
    // No socket yet. Open one and queue the payload so it is sent on open.
    pendingSendsRef.current.push(payload)
    if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
      connect()
    }
  }, [connect])

  useEffect(() => {
    if (autoConnect) connect()
    return () => disconnect()
  }, [autoConnect, connect, disconnect])

  return { connect, disconnect, send, lastMessage, isConnected }
}
