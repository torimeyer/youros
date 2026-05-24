import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../lib/api'

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
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastActivityRef = useRef(Date.now())
  const awayThresholdMs = 5 * 60 * 1000

  useEffect(() => {
    api.get<AdhdConfig>('/adhd/config').then(setConfig).catch(() => {})
  }, [])

  const updateConfig = useCallback(async (patch: Partial<AdhdConfig>) => {
    try {
      const updated = await api.patch<AdhdConfig>('/adhd/config', patch)
      setConfig(updated)
    } catch {
      // keep local state
    }
  }, [])

  const fetchCheckIn = useCallback(async () => {
    try {
      const data = await api.get<CheckInData>('/adhd/check-in')
      setCheckIn(data)
    } catch {
      // silent
    }
  }, [])

  const fetchContextRebuild = useCallback(async () => {
    try {
      const data = await api.get<ContextRebuild>('/adhd/context-rebuild')
      setContextRebuild(data)
    } catch {
      // silent
    }
  }, [])

  useEffect(() => {
    if (!config.enabled) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      return
    }

    fetchCheckIn()
    intervalRef.current = setInterval(fetchCheckIn, config.check_in_seconds * 1000)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [config.enabled, config.check_in_seconds, fetchCheckIn])

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
