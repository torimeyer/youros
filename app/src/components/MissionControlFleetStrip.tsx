import { useState, useEffect, useCallback, useMemo } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'
import { TodayDigestPanel, type DigestData } from '../pages/TodayDigestPanel'

interface RawSeat {
  id?: string
  session_id?: string
  name?: string
  agent?: string
  agent_name?: string
  label?: string
  type?: string
  runtime?: string
  provider?: string
  model?: string
  source?: string
  repo?: string
  project?: string
  cwd?: string
  worktree?: string
  workspace?: string
  status?: string
  activity?: string
  current_step?: string
  task?: string
  activity_count?: number
  touched_files?: string[]
  files?: string[]
  recent_files?: string[]
  files_touched?: string[]
  stuck?: boolean
  stalled?: boolean
  quiet?: boolean
  is_stalled?: boolean
}

interface RawConflict {
  path?: string
  session_ids?: string[]
  seats?: string[]
  agents?: string[]
}

interface SessionsResponse {
  sessions?: RawSeat[]
  seats?: RawSeat[]
  conflicts?: RawConflict[]
  file_conflicts?: RawConflict[]
  digest?: DigestData | null
}

interface TrustStatusResponse {
  ostk_available?: boolean
  humanfile_valid?: boolean
  humanfile_warning?: string | null
  ostk_boot_ok?: boolean
  ostk_trust?: {
    ostk_available?: boolean
    humanfile_valid?: boolean
    humanfile_warning?: string | null
    ostk_boot_ok?: boolean
  }
}

interface NormalizedSeat {
  key: string
  nudgeName: string
  displayName: string
  runtime: string
  repoBasename: string | null
  status: string
  active: boolean
  quiet: boolean
  activity: string
  touchedFiles: string[]
  activityCount: number
}

interface NormalizedConflict {
  path: string
  seats: string[]
}

function extractBasename(rawPath: string | undefined): string | null {
  if (!rawPath) return null
  const trimmed = rawPath.trim().replace(/\/+$/, '')
  if (!trimmed) return null
  const parts = trimmed.split('/')
  return parts[parts.length - 1] || trimmed
}

function detectRuntimeBadge(seat: RawSeat): string {
  const probe = [
    seat.runtime,
    seat.provider,
    seat.model,
    seat.source,
    seat.type,
    seat.id,
    seat.session_id,
    seat.name,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  if (probe.includes('gemini')) return 'Gemini'
  if (probe.includes('claude') || probe.includes('anthropic')) return 'Claude'
  if (seat.runtime && seat.runtime.trim()) {
    const clean = seat.runtime.trim()
    return clean.charAt(0).toUpperCase() + clean.slice(1)
  }
  return 'ostk'
}

function resolveNudgeTarget(seat: RawSeat): string {
  if (seat.agent_name && seat.agent_name.trim()) return seat.agent_name.trim()
  if (seat.agent && seat.agent.trim()) return seat.agent.trim()
  const rawName = (seat.name || '').trim()
  const rawId = (seat.id || seat.session_id || '').trim()
  if (rawName && !rawName.includes(' ')) return rawName
  if (rawId) return rawId
  return rawName || 'operator'
}

function normalizeSeats(payload: SessionsResponse | RawSeat[] | null): NormalizedSeat[] {
  const list: RawSeat[] = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.sessions)
      ? payload.sessions
      : Array.isArray(payload?.seats)
        ? payload.seats
        : []

  return list.map((s, idx) => {
    const nudgeName = resolveNudgeTarget(s)
    const displayName = (s.label || s.name || s.id || s.session_id || `seat-${idx + 1}`).trim()
    const status = (s.status || 'active').toLowerCase()
    const active = status === 'active' || status === 'running' || status === 'stalled' || status === 'quiet'
    const quiet = Boolean(
      s.stuck ||
        s.stalled ||
        s.quiet ||
        s.is_stalled ||
        status === 'stalled' ||
        status === 'quiet',
    )
    const fileCandidates = [
      ...(Array.isArray(s.touched_files) ? s.touched_files : []),
      ...(Array.isArray(s.files) ? s.files : []),
      ...(Array.isArray(s.recent_files) ? s.recent_files : []),
      ...(Array.isArray(s.files_touched) ? s.files_touched : []),
    ]
    const seen = new Set<string>()
    const touchedFiles: string[] = []
    for (const f of fileCandidates) {
      if (typeof f === 'string' && f.trim() && !seen.has(f.trim())) {
        seen.add(f.trim())
        touchedFiles.push(f.trim())
      }
    }

    const repoSource = s.repo || s.project || s.worktree || s.workspace || s.cwd
    return {
      key: (s.id || s.session_id || nudgeName || `seat-${idx}`).trim(),
      nudgeName,
      displayName,
      runtime: detectRuntimeBadge(s),
      repoBasename: extractBasename(repoSource),
      status,
      active,
      quiet,
      activity: (s.activity || s.current_step || s.task || '').trim(),
      touchedFiles,
      activityCount: typeof s.activity_count === 'number' ? s.activity_count : Math.max(1, touchedFiles.length),
    }
  })
}

function computeConflicts(
  payload: SessionsResponse | RawSeat[] | null,
  seats: NormalizedSeat[],
): NormalizedConflict[] {
  const byPath = new Map<string, Set<string>>()

  if (payload && !Array.isArray(payload)) {
    const explicit = [
      ...(Array.isArray(payload.conflicts) ? payload.conflicts : []),
      ...(Array.isArray(payload.file_conflicts) ? payload.file_conflicts : []),
    ]
    for (const c of explicit) {
      const p = (c.path || '').trim()
      if (!p) continue
      const holders = c.seats || c.agents || c.session_ids || []
      if (!Array.isArray(holders) || holders.length < 2) continue
      const set = byPath.get(p) ?? new Set<string>()
      for (const h of holders) {
        if (typeof h === 'string' && h.trim()) set.add(h.trim())
      }
      byPath.set(p, set)
    }
  }

  for (const seat of seats) {
    if (!seat.active) continue
    for (const filePath of seat.touchedFiles) {
      const set = byPath.get(filePath) ?? new Set<string>()
      set.add(seat.nudgeName)
      byPath.set(filePath, set)
    }
  }

  const out: NormalizedConflict[] = []
  for (const [path, holders] of byPath.entries()) {
    if (holders.size >= 2) {
      out.push({ path, seats: Array.from(holders) })
    }
  }
  return out
}

export default function MissionControlFleetStrip() {
  const [sessionsPayload, setSessionsPayload] = useState<SessionsResponse | RawSeat[] | null>(null)
  const [trustStatus, setTrustStatus] = useState<TrustStatusResponse | null>(null)
  const [digestData, setDigestData] = useState<DigestData | null>(null)
  const [digestOpen, setDigestOpen] = useState(false)
  const [lockedPaths, setLockedPaths] = useState<Record<string, boolean>>({})
  const [nudgedSeats, setNudgedSeats] = useState<Record<string, boolean>>({})

  const loadFleet = useCallback(async () => {
    const [primarySessions, keyStatus] = await Promise.all([
      Promise.resolve(api.get<SessionsResponse | RawSeat[]>('/sessions')).catch(() => null),
      Promise.resolve(api.get<TrustStatusResponse>('/secrets/key-status')).catch(() => null),
    ])

    let resolvedSessions = primarySessions
    if (resolvedSessions === null) {
      resolvedSessions = await Promise.resolve(
        api.get<SessionsResponse>('/sessions/coordination'),
      ).catch(() => null)
    }

    if (resolvedSessions !== null) {
      setSessionsPayload(resolvedSessions)
      if (!Array.isArray(resolvedSessions) && resolvedSessions.digest) {
        setDigestData(resolvedSessions.digest)
      }
    }
    if (keyStatus !== null) {
      setTrustStatus(keyStatus)
    }
  }, [])

  useEffect(() => {
    loadFleet()
    const timer = setInterval(loadFleet, 15000)
    return () => clearInterval(timer)
  }, [loadFleet])

  const seats = useMemo(() => normalizeSeats(sessionsPayload), [sessionsPayload])
  const activeSeats = useMemo(() => seats.filter((s) => s.active), [seats])
  const conflicts = useMemo(
    () => computeConflicts(sessionsPayload, seats),
    [sessionsPayload, seats],
  )

  const trustInvalid = useMemo(() => {
    if (!trustStatus) return false
    const nested = trustStatus.ostk_trust
    if (trustStatus.humanfile_valid === false || nested?.humanfile_valid === false) return true
    if (trustStatus.ostk_boot_ok === false || nested?.ostk_boot_ok === false) return true
    if (trustStatus.ostk_available === false || nested?.ostk_available === false) return true
    return false
  }, [trustStatus])

  const trustTooltip =
    trustStatus?.humanfile_warning ||
    trustStatus?.ostk_trust?.humanfile_warning ||
    (trustInvalid
      ? 'Run ostk trust sign --humanfile to restore verified secret injection.'
      : 'HUMANFILE signature and ostk substrate verified.')

  const handleToggleDigest = async () => {
    const next = !digestOpen
    setDigestOpen(next)
    if (next && !digestData) {
      const fetched = await Promise.resolve(api.get<DigestData>('/sessions/digest')).catch(
        () => null,
      )
      if (fetched) setDigestData(fetched)
    }
  }

  const effectiveDigest = useMemo<DigestData | null>(() => {
    if (
      digestData &&
      Array.isArray(digestData.sessions) &&
      Array.isArray(digestData.closed_tasks_today) &&
      (digestData.sessions.length > 0 || digestData.closed_tasks_today.length > 0)
    ) {
      return digestData
    }
    if (seats.length === 0) return null
    return {
      sessions: seats.map((s) => ({
        session_id: s.key,
        label: s.displayName,
        activity_count: s.activityCount,
        files_touched: s.touchedFiles,
        recent_activity: s.activity,
      })),
      closed_tasks_today: [],
      generated_at: new Date().toISOString(),
    }
  }, [digestData, seats])

  const handleAcquireLock = async (path: string) => {
    await Promise.resolve(api.post('/locks', { path, holder: 'operator' })).catch(() => null)
    setLockedPaths((prev) => ({ ...prev, [path]: true }))
  }

  const handleNudgeAgent = async (conflict: NormalizedConflict) => {
    const targetSeat = conflict.seats[0]
    if (!targetSeat) return
    const peers = conflict.seats.slice(1).join(', ')
    const message = peers
      ? `File conflict detected on ${conflict.path} with ${peers}. Coordinate or yield lock.`
      : `File conflict detected on ${conflict.path}. Coordinate or yield lock.`
    await Promise.resolve(
      api.post(`/agents/${encodeURIComponent(targetSeat)}/nudge`, { message }),
    ).catch(() => null)
    setNudgedSeats((prev) => ({ ...prev, [`${conflict.path}:${targetSeat}`]: true }))
  }

  return (
    <section
      data-testid="mission-control-strip"
      className="mb-6 rounded-xl border border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/60 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5">
            <Icon name="radar" className="text-blue-600 dark:text-blue-400" size={18} />
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-slate-200">
              Mission Control
            </span>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 dark:bg-slate-800 px-2.5 py-0.5 text-xs font-medium text-slate-700 dark:text-slate-300">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                activeSeats.length > 0 ? 'bg-emerald-400' : 'bg-slate-500'
              }`}
            />
            {activeSeats.length} active {activeSeats.length === 1 ? 'seat' : 'seats'}
          </span>
          <span
            data-testid="mission-control-trust-pill"
            title={trustTooltip}
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              trustInvalid
                ? 'border-amber-500/30 bg-amber-500/15 text-amber-500 dark:text-amber-400'
                : 'border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
            }`}
          >
            <Icon name={trustInvalid ? 'warning' : 'verified_user'} size={13} />
            {trustInvalid ? 'HUMANFILE signature needs ostk trust sign' : 'ostk verified'}
          </span>
        </div>

        <button
          type="button"
          data-testid="toggle-today-digest"
          aria-expanded={digestOpen}
          onClick={handleToggleDigest}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-800/80 px-2.5 py-1 text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
        >
          <Icon name="history" size={14} />
          Today across sessions
        </button>
      </div>

      {conflicts.length > 0 && (
        <div
          data-testid="mission-control-conflict-banner"
          className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3.5 py-2.5"
        >
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-500 dark:text-amber-400 mb-1.5">
            <Icon name="warning" size={15} />
            Active file conflict detected
          </div>
          <div className="space-y-2">
            {conflicts.map((conflict) => {
              const targetSeat = conflict.seats[0] || ''
              const isLocked = Boolean(lockedPaths[conflict.path])
              const isNudged = Boolean(nudgedSeats[`${conflict.path}:${targetSeat}`])
              return (
                <div
                  key={conflict.path}
                  className="flex flex-wrap items-center justify-between gap-2 text-xs"
                >
                  <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                    <span className="font-mono font-medium text-amber-600 dark:text-amber-300">
                      {conflict.path}
                    </span>
                    <span className="text-slate-500">touched by</span>
                    <span className="font-medium text-slate-800 dark:text-slate-200">
                      {conflict.seats.join(' and ')}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleAcquireLock(conflict.path)}
                      className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/20 px-2.5 py-1 text-xs font-medium text-amber-700 dark:text-amber-200 hover:bg-amber-500/30 transition-colors"
                    >
                      <Icon name="lock" size={13} />
                      {isLocked ? 'Lock Acquired' : 'Acquire Lock'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleNudgeAgent(conflict)}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-300 dark:border-slate-700 bg-white/80 dark:bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
                    >
                      <Icon name="notifications_active" size={13} />
                      {isNudged ? 'Nudged' : 'Nudge Agent'}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {seats.length > 0 && (
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
          {seats.map((seat) => (
            <div
              key={seat.key}
              data-testid={`mission-control-seat-${seat.key}`}
              className="rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900/90 px-3 py-2 text-xs"
            >
              <div className="flex items-center justify-between gap-1.5 flex-wrap">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span
                    className={`inline-block h-2 w-2 rounded-full shrink-0 ${
                      seat.active ? 'bg-emerald-400' : 'bg-slate-500'
                    }`}
                  />
                  <span className="font-medium text-slate-800 dark:text-slate-200 truncate">
                    {seat.displayName}
                  </span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      seat.runtime === 'Gemini'
                        ? 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-400'
                        : 'bg-purple-500/15 text-purple-600 dark:text-purple-400'
                    }`}
                  >
                    {seat.runtime}
                  </span>
                  {seat.repoBasename && (
                    <span
                      data-testid="seat-repo-badge"
                      className="rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 dark:text-slate-400"
                    >
                      {seat.repoBasename}
                    </span>
                  )}
                  {seat.quiet && (
                    <span
                      data-testid="seat-quiet-badge"
                      className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-500"
                    >
                      quiet
                    </span>
                  )}
                </div>
              </div>

              {seat.activity && (
                <div className="mt-1 truncate text-slate-600 dark:text-slate-400">
                  {seat.activity}
                </div>
              )}

              {seat.touchedFiles.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {seat.touchedFiles.slice(0, 3).map((file) => (
                    <span
                      key={file}
                      title={file}
                      className="rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-500 dark:text-slate-400 truncate max-w-[150px]"
                    >
                      {file.split('/').pop() || file}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {digestOpen && (
        <div className="mt-3">
          {effectiveDigest ? (
            <TodayDigestPanel digest={effectiveDigest} />
          ) : (
            <div
              data-testid="today-digest-empty"
              className="rounded-lg border border-blue-500/20 bg-blue-500/5 px-4 py-2.5 text-xs text-slate-500"
            >
              No cross-session activity recorded yet today.
            </div>
          )}
        </div>
      )}
    </section>
  )
}
