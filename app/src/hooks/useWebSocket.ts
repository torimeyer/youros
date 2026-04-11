import { useEffect, useState, useCallback, useRef } from 'react'
import { flushSync } from 'react-dom'

interface WSMessage {
  type: string
  data?: string | Record<string, unknown>
  usage?: { input_tokens: number; output_tokens: number }
  return_code?: number
}

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
  // Queue of payloads the caller tried to send before the socket finished
  // opening. Flushed in order from the onopen handler so nothing is ever
  // silently dropped on the very first send after connect().
  const pendingSendsRef = useRef<string[]>([])
  pathRef.current = path

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

    gotMessageRef.current = false
    hadErrorRef.current = false
    streamEndedRef.current = true

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}${pathRef.current}`)
    ws.onopen = () => {
      if ((ws as WebSocket & { _shouldCloseOnOpen?: boolean })._shouldCloseOnOpen) {
        ws.close()
        return
      }
      setIsConnected(true)
      // Flush any sends that were queued while the socket was still
      // connecting. Mark the stream as in progress for each so a mid-turn
      // drop becomes a real error instead of a silent done.
      if (pendingSendsRef.current.length > 0) {
        const queued = pendingSendsRef.current
        pendingSendsRef.current = []
        for (const payload of queued) {
          streamEndedRef.current = false
          ws.send(payload)
        }
      }
    }
    ws.onclose = () => {
      setIsConnected(false)
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      // If the server already finished the turn (sent done/error), closing
      // is normal and we just clear the streaming state with a silent done.
      // If the turn was still in progress, the socket dropped mid-stream,
      // so surface a real error instead of leaving an empty assistant bubble.
      if (streamEndedRef.current) {
        flushSync(() => {
          setLastMessage({ type: 'done' })
        })
      } else {
        streamEndedRef.current = true
        flushSync(() => {
          setLastMessage({
            type: 'error',
            data: 'Connection dropped before the response finished. Please try again.',
          })
        })
      }
    }
    ws.onerror = () => {
      hadErrorRef.current = true
      streamEndedRef.current = true
      flushSync(() => {
        setLastMessage({ type: 'error', data: 'Connection error. Please try again.' })
      })
    }
    ws.onmessage = (event) => {
      gotMessageRef.current = true
      const parsed = JSON.parse(event.data) as WSMessage
      // A fresh token event means a new turn has started streaming. Mark
      // the stream as in progress so a mid-turn close becomes an error.
      if (parsed.type === 'token' || parsed.type === 'thinking') {
        streamEndedRef.current = false
      } else if (parsed.type === 'done' || parsed.type === 'error') {
        streamEndedRef.current = true
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
  }, [])

  const disconnect = useCallback(() => {
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
  }, [])

  const send = useCallback((data: unknown) => {
    const payload = JSON.stringify(data)
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      // Mark the stream as in progress so a mid-turn close becomes an error
      // instead of a silent done. The server will flip this back to true
      // when it sends a real done or error event.
      streamEndedRef.current = false
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
