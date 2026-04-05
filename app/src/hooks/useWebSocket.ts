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

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}${path}`)
    ws.onopen = () => setIsConnected(true)
    ws.onclose = () => {
      setIsConnected(false)
      wsRef.current = null
    }
    ws.onmessage = (event) => setLastMessage(JSON.parse(event.data))
    wsRef.current = ws
  }, [path])

  const disconnect = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data))
  }, [])

  useEffect(() => {
    if (autoConnect) connect()
    return () => disconnect()
  }, [autoConnect, connect, disconnect])

  return { connect, disconnect, send, lastMessage, isConnected }
}
