import { useState, useEffect, useRef, useCallback } from 'react'

export interface AdhdConfig {
  enabled: boolean
  check_in_seconds: number
  focus_mode: boolean
}

interface CheckInData {
  running_count: number
  agents: Array<{
    name: string
    task: string
    current_step: string
  }>
  timestamp: string
}

interface ContextRebuild {
  active_agents: Array<{
    name: string
    task: string
    current_step: string
    status: string
  }>
  recent_agents: Array<{
    name: string
    task: string
    summary: string
    status: string
    completed_at: string
    output_path?: string
  }>
  in_progress_tasks: Array<{
    id: string
    title: string
    priority?: string
    labels: string[]
  }>
  last_chat: {
    role: string
    preview: string
    timestamp?: string
  } | null
  next_step: string
}

const DEFAULTS: AdhdConfig = {
  enabled: false,
  check_in_seconds: 30,
  focus_mode: false,
}

export function useAdhdMode() {
  const [config, setConfig] = useState<AdhdConfig>(DEFAULTS)
  const [checkIn, setCheckIn] = useState<CheckInData | null>(null)
  const [showWelcomeBack, setShowWelcomeBack] = useState(false)
  const [contextRebuild, setContextRebuild] = useState<ContextRebuild | null>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const lastActivityRef = useRef(Date.now())
  const awayThresholdMs = 5 * 60 * 1000

  useEffect(() => {
    fetch('/api/adhd/config')
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((data: AdhdConfig) => setConfig(data))
      .catch(() => {})
  }, [])

  const updateConfig = useCallback(async (patch: Partial<AdhdConfig>) => {
    try {
      const res = await fetch('/api/adhd/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) return
      const updated = (await res.json()) as AdhdConfig
      setConfig(updated)
    } catch {
      // keep local state
    }
  }, [])

  const fetchContextRebuild = useCallback(async () => {
    try {
      const res = await fetch('/api/adhd/context-rebuild')
      if (!res.ok) return
      const data = (await res.json()) as ContextRebuild
      setContextRebuild(data)
    } catch {
      // silent
    }
  }, [])

  useEffect(() => {
    if (!config.enabled) {
      if (pollRef.current !== null) {
        clearTimeout(pollRef.current)
        pollRef.current = null
      }
      if (pollAbortRef.current !== null) {
        pollAbortRef.current.abort()
        pollAbortRef.current = null
      }
      return
    }

    let backoff = 0

    const tick = async () => {
      const controller = new AbortController()
      pollAbortRef.current = controller
      try {
        const res = await fetch('/api/adhd/check-in', { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = (await res.json()) as CheckInData
        setCheckIn(data)
        backoff = 0
      } catch (e: unknown) {
        if (controller.signal.aborted) return
        backoff = backoff === 0 ? 1000 : Math.min(backoff * 2, 30_000)
      }
      if (!controller.signal.aborted) {
        pollRef.current = setTimeout(tick, backoff || config.check_in_seconds * 1000)
      }
    }

    tick()

    return () => {
      if (pollRef.current !== null) {
        clearTimeout(pollRef.current)
        pollRef.current = null
      }
      if (pollAbortRef.current !== null) {
        pollAbortRef.current.abort()
        pollAbortRef.current = null
      }
    }
  }, [config.enabled, config.check_in_seconds])

  useEffect(() => {
    if (!config.enabled) return

    const trackActivity = () => {
      lastActivityRef.current = Date.now()
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        const away = Date.now() - lastActivityRef.current
        if (away >= awayThresholdMs) {
          fetchContextRebuild()
          setShowWelcomeBack(true)
        }
        lastActivityRef.current = Date.now()
      } else {
        lastActivityRef.current = Date.now()
      }
    }

    window.addEventListener('mousemove', trackActivity, { passive: true })
    window.addEventListener('keydown', trackActivity, { passive: true })
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      window.removeEventListener('mousemove', trackActivity)
      window.removeEventListener('keydown', trackActivity)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [config.enabled, fetchContextRebuild, awayThresholdMs])

  const dismissWelcomeBack = useCallback(() => {
    setShowWelcomeBack(false)
  }, [])

  return {
    config,
    updateConfig,
    checkIn,
    showWelcomeBack,
    contextRebuild,
    dismissWelcomeBack,
    isAdhdMode: config.enabled,
    isFocusMode: config.enabled && config.focus_mode,
  }
}
