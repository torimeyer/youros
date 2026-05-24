import { useEffect } from 'react'

// C major ascending fanfare: C5 E5 G5 C6 G5 E5 C6
const NOTES = [
  { freq: 523.25, dur: 0.18 },
  { freq: 659.25, dur: 0.18 },
  { freq: 784.0,  dur: 0.18 },
  { freq: 1046.5, dur: 0.36 },
  { freq: 784.0,  dur: 0.18 },
  { freq: 659.25, dur: 0.18 },
  { freq: 1046.5, dur: 0.75 },
]

export function playFanfare(): void {
  const Ctx = (window as Window & { AudioContext?: typeof AudioContext }).AudioContext
  if (!Ctx) return
  const ctx = new Ctx()
  let t = ctx.currentTime + 0.05
  for (const { freq, dur } of NOTES) {
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(freq, t)
    gain.gain.setValueAtTime(0.35, t)
    gain.gain.linearRampToValueAtTime(0, t + dur - 0.02)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(t)
    osc.stop(t + dur)
    t += dur
  }
}

export function useVictoryFanfare(play = playFanfare): void {
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/api/ws/notifications`
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    const connect = () => {
      ws = new WebSocket(url)
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data as string)
          if (msg.type === 'needle_closed') {
            play()
          }
        } catch {
          // malformed frame — ignore
        }
      }
      ws.onclose = () => {
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 3000)
        }
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [])
}
