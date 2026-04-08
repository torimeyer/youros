import { useEffect, useState, useCallback, useRef } from 'react'

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
  pathRef.current = path

  const connect = useCallback(() => {
    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    gotMessageRef.current = false
    hadErrorRef.current = false
    streamEndedRef.current = true

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}${pathRef.current}`)
    ws.onopen = () => setIsConnected(true)
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
        setLastMessage({ type: 'done' })
      } else {
        streamEndedRef.current = true
        setLastMessage({
          type: 'error',
          data: 'Connection dropped before the response finished. Please try again.',
        })
      }
    }
    ws.onerror = () => {
      hadErrorRef.current = true
      streamEndedRef.current = true
      setLastMessage({ type: 'error', data: 'Connection error. Please try again.' })
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
      setLastMessage(parsed)
    }
    wsRef.current = ws
  }, [])

  const disconnect = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      // Mark the stream as in progress so a mid-turn close becomes an error
      // instead of a silent done. The server will flip this back to true
      // when it sends a real done or error event.
      streamEndedRef.current = false
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  useEffect(() => {
    if (autoConnect) connect()
    return () => disconnect()
  }, [autoConnect, connect, disconnect])

  return { connect, disconnect, send, lastMessage, isConnected }
}
