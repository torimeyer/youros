import { reportError } from './reportError'

/**
 * Shared, reference-counted WebSocket manager.
 *
 * The problem this solves: several React components and hooks want to
 * listen to the same backend channel (for example the notifications feed
 * and the task-finished chime both listen to /api/ws/notifications, and
 * the dashboard data feed is mounted both at the app shell and on the
 * dashboard page). Before this manager, each of those opened its own
 * socket, so a single channel could hold three or four connections at
 * once. React's development double-mount made it worse. Many duplicate
 * sockets plus their keepalive traffic were enough to saturate the
 * single-threaded backend and stop it answering new requests.
 *
 * The fix: there is exactly ONE socket per channel (keyed by its path).
 * The first listener opens it, every later listener shares it, and it is
 * closed only when the last listener goes away. Reconnect timing lives
 * here too, so a reconnect can never leak a second live socket for the
 * same channel.
 */

type Listener = {
  onMessage?: (data: unknown) => void
  onOpen?: () => void
  onClose?: () => void
}

type Channel = {
  url: string
  ws: WebSocket | null
  listeners: Set<Listener>
  reconnectTimer: ReturnType<typeof setTimeout> | null
  // True once the last listener has unsubscribed and the channel is being
  // torn down. Guards against a queued reconnect re-opening a dead channel.
  closing: boolean
}

const RECONNECT_MS = 5000

// One entry per channel path. A channel exists only while it has at least
// one listener; it is deleted from the map when the final listener leaves.
const channels = new Map<string, Channel>()

function wsUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

function openSocket(channel: Channel) {
  if (channel.closing) return
  // Never hold two live sockets for one channel. If one already exists in
  // a non-closed state, reuse it.
  if (
    channel.ws &&
    (channel.ws.readyState === WebSocket.OPEN ||
      channel.ws.readyState === WebSocket.CONNECTING)
  ) {
    return
  }

  let ws: WebSocket
  try {
    ws = new WebSocket(wsUrl(channel.url))
  } catch (e) {
    reportError(`Shared socket connect error (${channel.url})`, e)
    return
  }
  channel.ws = ws

  ws.onopen = () => {
    // A late-arriving open for a socket we already replaced or are tearing
    // down should not notify listeners.
    if (channel.ws !== ws || channel.closing) {
      try {
        ws.close()
      } catch {
        // ignore
      }
      return
    }
    for (const l of channel.listeners) l.onOpen?.()
  }

  ws.onmessage = (event: MessageEvent) => {
    if (channel.ws !== ws) return
    let parsed: unknown
    try {
      parsed = JSON.parse(event.data)
    } catch {
      // Non-JSON frame. Each listener decides what to do with raw frames;
      // we simply do not forward unparseable data.
      return
    }
    for (const l of channel.listeners) l.onMessage?.(parsed)
  }

  ws.onerror = () => {
    if (channel.ws !== ws) return
    // onclose handles reconnect; nothing extra needed here.
  }

  ws.onclose = () => {
    if (channel.ws === ws) channel.ws = null
    // Tell listeners the channel went down so they can start their own
    // HTTP fallback if they have one.
    for (const l of channel.listeners) l.onClose?.()
    // Only schedule a reconnect if the channel is still wanted (has
    // listeners) and is not being torn down. Exactly one reconnect timer
    // can be pending per channel, so a reconnect can never spawn a second
    // socket.
    if (channel.closing || channel.listeners.size === 0) return
    if (channel.reconnectTimer !== null) return
    channel.reconnectTimer = setTimeout(() => {
      channel.reconnectTimer = null
      openSocket(channel)
    }, RECONNECT_MS)
  }
}

function teardownChannel(channel: Channel) {
  channel.closing = true
  if (channel.reconnectTimer !== null) {
    clearTimeout(channel.reconnectTimer)
    channel.reconnectTimer = null
  }
  const ws = channel.ws
  channel.ws = null
  if (ws) {
    // Detach handlers so a close fired by our own close() call does not
    // notify listeners that are already gone or schedule a reconnect.
    ws.onopen = null
    ws.onmessage = null
    ws.onerror = null
    ws.onclose = null
    if (
      ws.readyState === WebSocket.OPEN ||
      ws.readyState === WebSocket.CONNECTING
    ) {
      try {
        ws.close()
      } catch {
        // ignore
      }
    }
  }
  channels.delete(channel.url)
}

/**
 * Subscribe to a shared channel. Returns an unsubscribe function.
 *
 * The first subscriber to a path opens the socket; later subscribers
 * share it. The socket closes when the last subscriber unsubscribes.
 */
export function subscribeSharedSocket(path: string, listener: Listener): () => void {
  let channel = channels.get(path)
  if (!channel) {
    channel = {
      url: path,
      ws: null,
      listeners: new Set(),
      reconnectTimer: null,
      closing: false,
    }
    channels.set(path, channel)
    channel.listeners.add(listener)
    openSocket(channel)
  } else {
    channel.listeners.add(listener)
    // If a previous teardown was in flight for this exact object but a new
    // subscriber arrived, make sure the socket is (re)opened.
    channel.closing = false
    openSocket(channel)
  }

  let unsubscribed = false
  return () => {
    if (unsubscribed) return
    unsubscribed = true
    const ch = channels.get(path)
    if (!ch) return
    ch.listeners.delete(listener)
    if (ch.listeners.size === 0) {
      teardownChannel(ch)
    }
  }
}

// Test-only: report how many live channels (one socket each) exist. Used
// by unit tests to assert there is exactly one socket per channel.
export function _activeChannelCount(): number {
  return channels.size
}

// Test-only: reset all channel state between tests.
export function _resetSharedSockets(): void {
  for (const ch of Array.from(channels.values())) {
    teardownChannel(ch)
  }
  channels.clear()
}
