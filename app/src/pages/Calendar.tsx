import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import GoogleSetupGuideModal from '../components/GoogleSetupGuideModal'
import { ConnectCard, LoadingState, EmptyState } from '../components/ui'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'
import { onCalendarChange } from '../lib/sidebarBus'

export interface CalendarEvent {
  id: string
  summary?: string
  start: { dateTime?: string; date?: string }
  end: { dateTime?: string; date?: string }
  location?: string
  htmlLink?: string
  hangoutLink?: string
  colorId?: string
}

interface AuthStatus {
  authenticated: boolean
  needs_reauth: boolean
  email: string | null
}

interface ConnectAuthUrl {
  url: string
}

function formatTime(dtStr?: string): string {
  if (!dtStr) return ''
  try {
    const d = new Date(dtStr)
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase()
  } catch {
    return dtStr.substring(11, 16)
  }
}

// Build the user-facing countdown subtitle for an event card.
//
// The old subtitle rendered the event's raw duration as "1h", which
// reads as random noise under the start time. A countdown makes it
// immediately useful: it tells you how soon the event is, and what to
// do right now.
//
// Returns an empty string when the event is already over, so callers
// can hide the subtitle entirely.
//
// ``now`` is injected so tests can freeze time without monkey-patching
// Date.now.
export function formatCountdown(
  start?: string,
  end?: string,
  now: number = Date.now(),
): string {
  if (!start) return ''
  try {
    const startMs = new Date(start).getTime()
    const endMs = end ? new Date(end).getTime() : startMs
    if (Number.isNaN(startMs) || Number.isNaN(endMs)) return ''
    // Already over: hide the subtitle.
    if (now >= endMs) return ''
    // In progress.
    if (now >= startMs) return 'Happening now'
    const msToStart = startMs - now
    const minsToStart = Math.round(msToStart / 60000)
    // Under 5 minutes reads as "right now" to the user. Keep it short.
    if (minsToStart < 5) return 'Starts soon'
    // 5 to 30 minutes out: show the minute count so the user can plan.
    if (minsToStart <= 30) return `Starts in ${minsToStart} minutes`
    // 31 to 60 minutes out: round up to the 1 hour callout.
    if (minsToStart <= 60) return 'Coming up in 1 hour'
    // Over an hour out: show hours. Round to the nearest hour so 1h 20m
    // reads as "1 hour" and 1h 40m reads as "2 hours", matching how a
    // person would describe it.
    const hours = Math.max(1, Math.round(minsToStart / 60))
    if (hours === 1) return 'Coming up in 1 hour'
    return `Coming up in ${hours} hours`
  } catch {
    return ''
  }
}

// Pick the right color and shape for the countdown badge based on how
// close the event is. Returns null when the subtitle should be hidden
// entirely (after the event ends, or when there is no start).
//
// Colors mirror the rest of yourOS chips:
//   red   = under 5 minutes, you have to leave now
//   amber = in 5 to 30 minutes, time to wrap up
//   blue  = in 30 to 60 minutes, keep an eye on the clock
//   slate = more than an hour out, informational only
//   green = happening now, with a pulsing dot
//
// ``pulse`` asks the caller to render a pulsing dot in place of the
// clock icon so the in-progress badge reads as live.
export function countdownBadgeStyle(
  start?: string,
  end?: string,
  now: number = Date.now(),
): { className: string; pulse: boolean } | null {
  if (!start) return null
  try {
    const startMs = new Date(start).getTime()
    const endMs = end ? new Date(end).getTime() : startMs
    if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null
    // Already over: hide entirely.
    if (now >= endMs) return null
    // In progress.
    if (now >= startMs) {
      return {
        className: 'bg-green-500/20 text-green-400 border border-green-500/40',
        pulse: true,
      }
    }
    const minsToStart = Math.round((startMs - now) / 60000)
    if (minsToStart < 5) {
      return {
        className: 'bg-red-500/20 text-red-400 border border-red-500/40',
        pulse: false,
      }
    }
    if (minsToStart <= 30) {
      return {
        className: 'bg-amber-500/20 text-amber-400 border border-amber-500/40',
        pulse: false,
      }
    }
    if (minsToStart <= 60) {
      return {
        className: 'bg-blue-500/20 text-blue-400 border border-blue-500/40',
        pulse: false,
      }
    }
    return {
      className: 'bg-slate-500/20 text-slate-400 border border-slate-500/40',
      pulse: false,
    }
  } catch {
    return null
  }
}

// Small inline SVG clock glyph for the countdown badge. Inline SVG
// avoids pulling the material icon font's text glyph into
// ``textContent``, which would break assertions that expect the
// badge to read exactly "Coming up in 1 hour".
function CountdownClockIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      data-testid="countdown-clock-icon"
    >
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15 14" />
    </svg>
  )
}

function dayLabel(dateStr: string): string {
  const d = new Date(dateStr)
  const today = new Date()
  const tomorrow = new Date(today)
  tomorrow.setDate(today.getDate() + 1)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow'
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}

function isWithin24Hours(ev: CalendarEvent): boolean {
  const startStr = ev.start?.dateTime || ev.start?.date
  if (!startStr) return false
  const start = new Date(startStr).getTime()
  const now = Date.now()
  return start > now && start - now <= 24 * 60 * 60 * 1000
}

function isNowInEvent(ev: CalendarEvent): boolean {
  const startStr = ev.start?.dateTime
  const endStr = ev.end?.dateTime
  if (!startStr || !endStr) return false
  const now = Date.now()
  return new Date(startStr).getTime() <= now && now <= new Date(endStr).getTime()
}

// Return the event's start date as a local YYYY-MM-DD string.
//
// CRITICAL: a naive ``startStr.substring(0, 10)`` reads the literal ISO
// date prefix, which is wrong for two different reasons:
//   1. If the backend hands back a UTC string ending in ``Z``, an event
//      at 11:30 AM local the next day can share the same UTC date as
//      the viewer's late-evening ``new Date().toISOString()`` day.
//   2. If the backend hands back an offset string like ``-07:00``, the
//      prefix is already the viewer's local date, which is right by
//      coincidence but would drift for any other viewer tz.
// Parsing through ``new Date`` then pulling local components gives the
// calendar day the viewer is actually living in. Regression guard for
// needle 282.
export function toLocalDateKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function getEventDate(ev: CalendarEvent): string {
  const startStr = ev.start?.dateTime || ev.start?.date || ''
  if (!startStr) return ''
  // All-day events use ``date`` (no time component), which is already
  // a calendar day with no tz. Trust the literal prefix so a full-day
  // event never slides into the prior/next local day through a
  // timezone detour.
  if (!ev.start?.dateTime && ev.start?.date) {
    return startStr.substring(0, 10)
  }
  const parsed = new Date(startStr)
  if (Number.isNaN(parsed.getTime())) return startStr.substring(0, 10)
  return toLocalDateKey(parsed)
}

// Seed the event list from localStorage so the page paints rows
// immediately on a return visit instead of showing a spinner while
// the Calendar API (or the backend cache) responds. The backend
// server cache lives in ~/.myos/calendar_cache and uses a 15 minute
// TTL, which means on every cache miss the user waits 400ms for the
// disk-backed round trip. Painting from localStorage hides that
// latency entirely. Same pattern as Gmail/Tasks. Needle 316.
const CALENDAR_CACHE_KEY = 'myos.calendarCache.v1'

function readCalendarCache(): CalendarEvent[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(CALENDAR_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as CalendarEvent[]) : []
  } catch {
    return []
  }
}

function writeCalendarCache(events: CalendarEvent[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(CALENDAR_CACHE_KEY, JSON.stringify(events))
  } catch {
    // Quota or serialization errors are not fatal.
  }
}


export default function Calendar() {
  const [searchParams] = useSearchParams()
  // If we have events in localStorage the user has connected before, so
  // optimistically mark the page as authenticated while the real auth
  // status round trip runs in the background. Skips the 100ms
  // "Checking connection..." spinner on every return visit. If the
  // real status comes back unauthenticated we switch to the connect
  // screen. Same pattern as Gmail/Tasks.
  const hasInitialCache = typeof window !== 'undefined' && readCalendarCache().length > 0
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(
    hasInitialCache ? { authenticated: true, needs_reauth: false, email: null } : null,
  )
  // Paint rows instantly from localStorage on a return visit. Background
  // fetch refreshes the list without blocking the first render.
  const [events, setEvents] = useState<CalendarEvent[]>(() => readCalendarCache())
  const [loading, setLoading] = useState<boolean>(() => readCalendarCache().length === 0)
  const [syncing, setSyncing] = useState(false)
  const [lastSynced, setLastSynced] = useState<Date | null>(null)
  const [createTaskStatus, setCreateTaskStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [prepStatus, setPrepStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [prepBriefings, setPrepBriefings] = useState<Record<string, string>>({})
  const [expandedPrep, setExpandedPrep] = useState<Record<string, boolean>>({})
  const [apiNotEnabled, setApiNotEnabled] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [briefingTaskStatus, setBriefingTaskStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [briefingTaskCount, setBriefingTaskCount] = useState<Record<string, number>>({})
  const [undoDelete, setUndoDelete] = useState<{
    event: CalendarEvent;
    timer: ReturnType<typeof setTimeout>;
  } | null>(null)
  // Track every event id the user has removed in the UI but whose delete
  // has not yet been fully confirmed by the backend. Two phases feed this
  // set:
  //   1. The optimistic-remove during the 5s undo window.
  //   2. The moment the delete API call is actually issued (timer fires
  //      or a second delete forces the previous one to commit).
  // Until the API replies, any refetch of the events list (chat creates
  // a new event during the undo window, the Sync button fires, focus
  // rehydrate) MUST filter these ids out. Without this the just-deleted
  // event reappears on the Calendar page mid-undo, which reads as the
  // delete "didn't work" even though it is about to. The ref survives
  // re-renders and is read synchronously from the fetch callback.
  const pendingDeletedIdsRef = useRef<Set<string>>(new Set())
  // Show the shared Google setup guide modal when the user clicks the
  // secondary "Need setup help?" link on the connect panel.
  const [showSetupGuide, setShowSetupGuide] = useState(false)
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false)
  // Ticker that forces the countdown subtitle to refresh once a minute.
  // A minute is fine grained enough for "Starts in 12 minutes" to stay
  // accurate without causing noisy re-renders.
  const [nowMs, setNowMs] = useState<number>(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    api.get<{ google_oauth_available?: boolean }>('/secrets/key-status')
      .then((data) => setGoogleOAuthAvailable(data.google_oauth_available ?? false))
      .catch(() => {})
  }, [])

  // Purge an event id from the localStorage seed cache. Without this,
  // the next page load paints the deleted event back on screen for ~400
  // ms while the background fetch runs, which reads as the delete
  // "didn't stick." Called once we know the delete committed server-side
  // (either we got 200 back, or Google reported 404 / already gone).
  const purgeCachedEvent = (id: string) => {
    try {
      if (typeof window === 'undefined' || !window.localStorage) return
      const cached = readCalendarCache()
      if (!cached.length) return
      const next = cached.filter((e) => e.id !== id)
      if (next.length === cached.length) return
      if (next.length === 0) {
        window.localStorage.removeItem(CALENDAR_CACHE_KEY)
      } else {
        writeCalendarCache(next)
      }
    } catch {
      // localStorage is best-effort; a failure here is not fatal.
    }
  }

  const deleteEvent = (ev: CalendarEvent) => {
    // If a previous undo window is still open, commit that delete
    // immediately and mark its id as pending so an overlapping refetch
    // cannot resurrect it on the list.
    if (undoDelete) {
      clearTimeout(undoDelete.timer)
      const pendingId = undoDelete.event.id
      pendingDeletedIdsRef.current.add(pendingId)
      api.delete(`/calendar/events/${encodeURIComponent(pendingId)}`)
        .then(() => purgeCachedEvent(pendingId))
        .catch((e) => reportError('Failed to delete calendar event', e))
        .finally(() => {
          pendingDeletedIdsRef.current.delete(pendingId)
        })
    }
    // Optimistically remove from the list AND record the id so any
    // concurrent refetch (chat creates an event, Sync fires, focus
    // rehydrate) keeps it hidden until the backend confirms the delete.
    // Without this, a refetch during the 5s undo window re-paints the
    // deleted event and the user thinks the delete silently failed.
    pendingDeletedIdsRef.current.add(ev.id)
    setEvents((prev) => prev.filter((e) => e.id !== ev.id))
    const timer = setTimeout(async () => {
      try {
        await api.delete(`/calendar/events/${encodeURIComponent(ev.id)}`)
        purgeCachedEvent(ev.id)
      } catch (e) {
        reportError('Failed to delete calendar event', e)
        // The delete truly failed server-side. Clear the pending mark
        // so the next refetch restores the event, and fetch now so the
        // user sees the correct state instead of a ghosted row.
        pendingDeletedIdsRef.current.delete(ev.id)
        await fetchEvents()
        setUndoDelete(null)
        return
      }
      pendingDeletedIdsRef.current.delete(ev.id)
      setUndoDelete(null)
    }, 5000)
    setUndoDelete({ event: ev, timer })
  }

  const handleUndoDeleteEvent = () => {
    if (!undoDelete) return
    clearTimeout(undoDelete.timer)
    // Drop the pending mark BEFORE restoring the event so a refetch
    // racing with the undo click does not double-filter it out.
    pendingDeletedIdsRef.current.delete(undoDelete.event.id)
    setEvents((prev) => [...prev, undoDelete.event].sort((a, b) => {
      const aTime = a.start?.dateTime || a.start?.date || ''
      const bTime = b.start?.dateTime || b.start?.date || ''
      return aTime.localeCompare(bTime)
    }))
    setUndoDelete(null)
  }

  const handleCreateTasksFromBriefing = async (eventId: string) => {
    setBriefingTaskStatus((prev) => ({ ...prev, [eventId]: 'loading' }))
    try {
      const res = await api.post<{ ok: boolean; tasks_created: number; tasks: { task_id: string; title: string }[] }>(
        `/meeting-prep/${eventId}/create-tasks`,
        {},
      )
      setBriefingTaskStatus((prev) => ({ ...prev, [eventId]: 'done' }))
      setBriefingTaskCount((prev) => ({ ...prev, [eventId]: res.tasks_created }))
    } catch {
      setBriefingTaskStatus((prev) => ({ ...prev, [eventId]: 'error' }))
    }
  }

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<AuthStatus>('/calendar/auth/status')
      setAuthStatus(res)
    } catch {
      setAuthStatus({ authenticated: false, needs_reauth: false, email: null })
    }
  }, [])

  const fetchEvents = useCallback(async () => {
    try {
      const res = await api.get<{ events: CalendarEvent[] }>('/calendar/events')
      let evs = res.events || []
      // If the cache hands back an empty list, force a sync once to
      // bust any stale empty cache from a previous transient blip.
      // Without this, a single bad fetch earlier in the day would
      // leave the Calendar tab blank for the next 15 minutes even
      // though the user has real events. Regression guard for the
      // "Calendar tab shows nothing" demo bug.
      if (evs.length === 0) {
        try {
          await api.post('/calendar/sync', {})
          const retry = await api.get<{ events: CalendarEvent[] }>('/calendar/events')
          evs = retry.events || []
        } catch {
          // Keep evs as the original empty list.
        }
      }
      // Strip any events the user has removed in the UI but whose
      // delete has not yet been confirmed server-side. Google can take
      // up to a couple seconds after a successful DELETE to stop
      // returning a recurring-instance id, and during the 5s undo
      // window we have not even issued the delete yet. Filtering here
      // keeps the UI honest: events the user deleted stay gone even
      // if some other surface (chat, Sync button, focus rehydrate)
      // triggered a refetch mid-window.
      const pending = pendingDeletedIdsRef.current
      if (pending.size > 0) {
        evs = evs.filter((e) => !pending.has(e.id))
      }
      setEvents(evs)
      // Persist for the next page load so rows paint instantly.
      // Skip persisting an empty list so a transient zero-result fetch
      // does not pin the UI blank on the next visit.
      if (evs.length > 0) writeCalendarCache(evs)
      setLastSynced(new Date())
      setApiNotEnabled(false)
    } catch (err: unknown) {
      // Keep existing cached events on failure so a timeout does not
      // blank the UI. Only clear if we have nothing cached.
      setEvents((prev) => prev.length > 0 ? prev : [])
      const detail = (err as { response?: { data?: { detail?: { api_not_enabled?: boolean } } } })?.response?.data?.detail
      if (detail?.api_not_enabled) setApiNotEnabled(true)
    }
  }, [])

  useEffect(() => {
    // Only show the loading spinner when we have no cached rows. When
    // cache is present the page paints immediately and the background
    // fetch updates rows when it arrives. Same pattern as Gmail.
    const hasCached = readCalendarCache().length > 0
    if (!hasCached) setLoading(true)
    ;(async () => {
      try {
        // Fire status and events in parallel once we know the user has
        // cached auth. The old code waited for the status round trip
        // (~100ms) before even starting the events round trip (~400ms).
        // Kicking them off together shaves ~100ms off the cold page
        // load. If auth comes back unauthenticated we throw away the
        // events promise silently.
        const statusPromise = api.get<AuthStatus>('/calendar/auth/status')
        // Speculatively fire the events fetch. It is cheap when the
        // backend has a fresh cache and gets thrown away if the user
        // is not authenticated.
        const eventsPromise = hasCached
          ? fetchEvents()
          : statusPromise.then((s) =>
              s.authenticated && !s.needs_reauth ? fetchEvents() : undefined,
            )
        const status = await statusPromise
        setAuthStatus(status)
        if (status.authenticated && !status.needs_reauth) {
          await eventsPromise
        }
      } catch {
        setAuthStatus({ authenticated: false, needs_reauth: false, email: null })
      }
      setLoading(false)
    })()
  }, [fetchEvents])

  // Handle ?connected=true redirect back from OAuth
  useEffect(() => {
    if (searchParams.get('connected') === 'true') {
      fetchStatus().then(() => fetchEvents())
    }
  }, [searchParams, fetchStatus, fetchEvents])

  // Refetch when another surface (chat's create_calendar_event tool,
  // a direct write through /calendar, or another tab) signals a change.
  // Without this the Calendar tab stays stale after chat creates an
  // event until the user reloads. Needle: "calendar tab sync after
  // chat".
  useEffect(() => {
    const unsubscribe = onCalendarChange(() => {
      fetchEvents()
    })
    return unsubscribe
  }, [fetchEvents])

  const handleConnect = async () => {
    setConnectError(null)
    try {
      const res = await api.get<ConnectAuthUrl>('/drive/auth/url/calendar')
      window.location.href = res.url
    } catch {
      setConnectError(
        'Could not get the sign-in link. Make sure the yourOS backend is running and your Google credentials file is saved at ~/.myos/google_credentials.json.'
      )
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    try {
      await api.post('/calendar/sync', {})
      await fetchEvents()
    } catch {
      // ignore
    } finally {
      setSyncing(false)
    }
  }

  const handleCreateTask = async (ev: CalendarEvent, dayLabel: string, timeStr: string) => {
    const eventId = ev.id
    setCreateTaskStatus((prev) => ({ ...prev, [eventId]: 'loading' }))
    const title = `Prep for: ${ev.summary || 'Untitled'} (${dayLabel} ${timeStr})`
    try {
      await api.post('/tasks', { title, priority: 'P1' })
      setCreateTaskStatus((prev) => ({ ...prev, [eventId]: 'done' }))
    } catch {
      setCreateTaskStatus((prev) => ({ ...prev, [eventId]: 'error' }))
    }
  }

  const handlePrep = async (ev: CalendarEvent) => {
    const eventId = ev.id
    // Toggle: if briefing already loaded, expand/collapse.
    if (prepBriefings[eventId]) {
      setExpandedPrep((prev) => ({ ...prev, [eventId]: !prev[eventId] }))
      return
    }
    setPrepStatus((prev) => ({ ...prev, [eventId]: 'loading' }))
    try {
      const res = await api.post<{ briefing: string; event_title: string }>(
        '/meeting-prep',
        { event_id: eventId },
      )
      setPrepBriefings((prev) => ({ ...prev, [eventId]: res.briefing }))
      setExpandedPrep((prev) => ({ ...prev, [eventId]: true }))
      setPrepStatus((prev) => ({ ...prev, [eventId]: 'done' }))
    } catch {
      setPrepStatus((prev) => ({ ...prev, [eventId]: 'error' }))
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-3 sm:p-4 rounded-xl'

  // Group events by day. ``toISOString()`` would give UTC, so at 9pm
  // PDT on April 10 the UTC date is already April 11 and an event at
  // 11:30 AM local April 11 would collide with it. Use the local
  // calendar day the viewer is actually on. Regression guard for
  // needle 282.
  const todayStr = toLocalDateKey(new Date())
  const todayEvents = events.filter((ev) => getEventDate(ev) === todayStr)
  const upcomingGroups: Record<string, CalendarEvent[]> = {}
  events.forEach((ev) => {
    const d = getEventDate(ev)
    if (d !== todayStr) {
      if (!upcomingGroups[d]) upcomingGroups[d] = []
      upcomingGroups[d].push(ev)
    }
  })

  if (loading || authStatus === null) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (!authStatus?.authenticated || authStatus.needs_reauth) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="calendar_month"
            accentColor="#3b82f6"
            title={authStatus?.needs_reauth ? 'Calendar access needs to be updated' : 'Connect Google Calendar'}
            description={
              authStatus?.needs_reauth
                ? 'Reconnect your Google account to give yourOS permission to read your calendar. This uses the same account you already connected for Drive.'
                : 'See your upcoming meetings and create needles from events. This uses the same Google account as Drive, so no extra credentials are needed.'
            }
            primaryAction={
              <div className="w-full space-y-3">
                {googleOAuthAvailable ? (
                  <button
                    onClick={handleConnect}
                    className="w-full py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
                    data-testid="connect-google-button-calendar"
                  >
                    {authStatus?.needs_reauth ? 'Reconnect' : 'Connect Google account'}
                  </button>
                ) : (
                  <button
                    onClick={handleConnect}
                    className="w-full py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
                  >
                    {authStatus?.needs_reauth ? 'Reconnect' : 'Connect Google account'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowSetupGuide(true)}
                  className="w-full text-xs text-slate-500 hover:text-slate-300 underline"
                >
                  Need setup help? See the guide
                </button>
              </div>
            }
            error={connectError ?? undefined}
          />
        </div>
        {showSetupGuide && <GoogleSetupGuideModal onClose={() => setShowSetupGuide(false)} />}
      </div>
    )
  }

  if (apiNotEnabled) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="warning"
            accentColor="#f59e0b"
            title="Calendar API not enabled"
            description="Your Google Cloud project has Google Calendar API disabled. You need to enable it once. It only takes a minute."
            primaryAction={
              <div className="w-full space-y-3">
                <a
                  href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
                  target="_blank"
                  rel="noreferrer"
                  className="w-full block text-center py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
                >
                  Enable Calendar API in Google Cloud
                </a>
                <p className="text-xs text-slate-500 text-center">
                  After clicking Enable on Google's page, wait 1-2 minutes, then click Retry.
                </p>
                <button
                  onClick={() => { setApiNotEnabled(false); fetchEvents() }}
                  className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors"
                >
                  Retry
                </button>
              </div>
            }
          />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Calendar" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header row */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">Calendar</h1>
            {authStatus.email && (
              <p className="text-sm text-slate-400 mt-0.5">{authStatus.email}</p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {lastSynced && (
              <span className="text-xs text-slate-500">
                Synced {lastSynced.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            )}
            <button
              onClick={handleSync}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
            >
              <Icon name="sync" size={16} className={syncing ? 'animate-spin' : ''} />
              Sync
            </button>
          </div>
        </div>

        {/* Today's events strip */}
        <div className={`${cardClass} mb-6`}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="today" className="text-blue-400" size={18} />
            <h2 className="text-base font-semibold">Today</h2>
          </div>
          {todayEvents.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing on your calendar today.</p>
          ) : (
            <div className="space-y-3">
              {todayEvents.map((ev) => {
                const startTime = formatTime(ev.start?.dateTime)
                const countdown = formatCountdown(ev.start?.dateTime, ev.end?.dateTime, nowMs)
                const badge = countdownBadgeStyle(ev.start?.dateTime, ev.end?.dateTime, nowMs)
                const inProgress = isNowInEvent(ev)
                const taskState = createTaskStatus[ev.id]
                const within24h = isWithin24Hours(ev) || inProgress
                const pStatus = prepStatus[ev.id]
                const briefing = prepBriefings[ev.id]
                const expanded = expandedPrep[ev.id]
                return (
                  <div key={ev.id} className="space-y-2">
                    <div
                      className={`flex items-start gap-4 p-3 rounded-xl border transition-colors ${
                        inProgress
                          ? 'border-blue-500/50 bg-blue-500/10'
                          : 'border-slate-800 bg-slate-900/30'
                      }`}
                      data-testid={`event-card-${ev.id}`}
                    >
                      <div
                        className="flex-1 min-w-0 space-y-1"
                        data-testid={`event-body-${ev.id}`}
                      >
                        <div className="flex items-center gap-3">
                          <p className="text-sm font-medium text-slate-200 shrink-0">{startTime}</p>
                          <p className="font-medium text-sm truncate">{ev.summary || 'Untitled'}</p>
                          {inProgress && (
                            <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded-full shrink-0">
                              Now
                            </span>
                          )}
                        </div>
                        {countdown && badge && (
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                            data-testid={`countdown-badge-${ev.id}`}
                          >
                            {badge.pulse ? (
                              <span
                                className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse"
                                aria-hidden="true"
                                data-testid={`countdown-pulse-${ev.id}`}
                              />
                            ) : (
                              <CountdownClockIcon />
                            )}
                            <span data-testid={`countdown-${ev.id}`}>{countdown}</span>
                          </span>
                        )}
                        {ev.location && (
                          <p className="text-xs text-slate-400 truncate">{ev.location}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {ev.hangoutLink && (
                          <a
                            href={ev.hangoutLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 px-2 py-1 bg-green-500/20 text-green-400 rounded-lg text-xs hover:bg-green-500/30 transition-colors"
                          >
                            <Icon name="video_call" size={14} />
                            Meet
                          </a>
                        )}
                        {within24h && (
                          <button
                            onClick={() => handlePrep(ev)}
                            disabled={pStatus === 'loading'}
                            className="flex items-center gap-1 px-2 py-1 bg-violet-500/20 text-violet-300 hover:bg-violet-500/30 rounded-lg text-xs transition-colors disabled:opacity-50"
                            title="Get ready for this meeting"
                          >
                            {pStatus === 'loading' ? (
                              <><Icon name="progress_activity" size={14} className="animate-spin" /> Getting ready...</>
                            ) : pStatus === 'error' ? (
                              <><Icon name="error" size={14} className="text-red-400" /> Error</>
                            ) : briefing ? (
                              <><Icon name="auto_awesome" size={14} /> {expanded ? 'Hide' : 'Prep'}</>
                            ) : (
                              <><Icon name="auto_awesome" size={14} /> Prep</>
                            )}
                          </button>
                        )}
                        <button
                          onClick={() => handleCreateTask(ev, 'Today', startTime)}
                          disabled={taskState === 'loading' || taskState === 'done'}
                          className="flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs transition-colors disabled:opacity-50"
                          title="Create a prep needle"
                        >
                          {taskState === 'done' ? (
                            <><Icon name="check" size={14} className="text-green-400" /> Done</>
                          ) : taskState === 'error' ? (
                            <><Icon name="error" size={14} className="text-red-400" /> Error</>
                          ) : (
                            <><Icon name="add_task" size={14} /> Needle</>
                          )}
                        </button>
                        <button
                          onClick={() => deleteEvent(ev)}
                          className="flex items-center gap-1 px-2 py-1 text-slate-500 hover:text-red-400 rounded-lg text-xs transition-colors"
                          title="Delete this event"
                          data-testid={`delete-event-${ev.id}`}
                        >
                          <Icon name="delete" size={14} />
                        </button>
                      </div>
                    </div>
                    {briefing && expanded && (
                      <div className="bg-violet-500/10 border border-violet-500/20 rounded-xl p-3 space-y-2">
                        <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">{briefing}</p>
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => navigator.clipboard.writeText(briefing)}
                            className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
                          >
                            <Icon name="content_copy" size={12} />
                            Copy
                          </button>
                          <button
                            onClick={() => handleCreateTasksFromBriefing(ev.id)}
                            disabled={briefingTaskStatus[ev.id] === 'loading' || briefingTaskStatus[ev.id] === 'done'}
                            className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
                          >
                            {briefingTaskStatus[ev.id] === 'loading' ? (
                              <><Icon name="progress_activity" size={12} className="animate-spin" /> Creating needles...</>
                            ) : briefingTaskStatus[ev.id] === 'done' ? (
                              <><Icon name="check_circle" size={12} className="text-green-400" /> {briefingTaskCount[ev.id] || 0} needles created</>
                            ) : briefingTaskStatus[ev.id] === 'error' ? (
                              <><Icon name="error" size={12} className="text-red-400" /> Failed</>
                            ) : (
                              <><Icon name="add_task" size={12} /> Create needles from briefing</>
                            )}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Next 7 days grouped by day */}
        {Object.keys(upcomingGroups).length > 0 && (
          <div className="space-y-4">
            <h2 className="text-base font-semibold text-slate-300">Upcoming</h2>
            {Object.entries(upcomingGroups).map(([dateKey, dayEvents]) => {
              const label = dayLabel(dateKey + 'T12:00:00')
              return (
                <div key={dateKey} className={cardClass}>
                  <p className="text-sm font-medium text-slate-400 mb-3">{label}</p>
                  <div className="space-y-2">
                    {dayEvents.map((ev) => {
                      const startTime = formatTime(ev.start?.dateTime)
                      const countdown = formatCountdown(ev.start?.dateTime, ev.end?.dateTime, nowMs)
                      const badge = countdownBadgeStyle(ev.start?.dateTime, ev.end?.dateTime, nowMs)
                      const taskState = createTaskStatus[ev.id]
                      const within24h = isWithin24Hours(ev)
                      const pStatus = prepStatus[ev.id]
                      const briefing = prepBriefings[ev.id]
                      const expanded = expandedPrep[ev.id]
                      return (
                        <div key={ev.id} className="space-y-1.5">
                          <div
                            className="flex items-start gap-3 p-2.5 rounded-lg border border-slate-800/50 bg-slate-900/20"
                            data-testid={`event-card-${ev.id}`}
                          >
                            <div
                              className="flex-1 min-w-0 space-y-1"
                              data-testid={`event-body-${ev.id}`}
                            >
                              <div className="flex items-center gap-3">
                                <p className="text-xs font-medium text-slate-300 shrink-0">{startTime}</p>
                                <p className="text-sm truncate">{ev.summary || 'Untitled'}</p>
                              </div>
                              {countdown && badge && (
                                <span
                                  className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}
                                  data-testid={`countdown-badge-${ev.id}`}
                                >
                                  {badge.pulse ? (
                                    <span
                                      className="h-1 w-1 rounded-full bg-green-400 animate-pulse"
                                      aria-hidden="true"
                                      data-testid={`countdown-pulse-${ev.id}`}
                                    />
                                  ) : (
                                    <CountdownClockIcon />
                                  )}
                                  <span data-testid={`countdown-${ev.id}`}>{countdown}</span>
                                </span>
                              )}
                              {ev.location && (
                                <p className="text-xs text-slate-500 truncate">{ev.location}</p>
                              )}
                            </div>
                            <div className="flex items-center gap-2 shrink-0">
                              {ev.hangoutLink && (
                                <a
                                  href={ev.hangoutLink}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center gap-1 px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded text-xs hover:bg-green-500/30 transition-colors"
                                >
                                  <Icon name="video_call" size={12} />
                                  Meet
                                </a>
                              )}
                              {within24h && (
                                <button
                                  onClick={() => handlePrep(ev)}
                                  disabled={pStatus === 'loading'}
                                  className="flex items-center gap-1 px-1.5 py-0.5 bg-violet-500/20 text-violet-300 hover:bg-violet-500/30 rounded text-xs transition-colors disabled:opacity-50"
                                  title="Get ready for this meeting"
                                >
                                  {pStatus === 'loading' ? (
                                    <Icon name="progress_activity" size={12} className="animate-spin" />
                                  ) : pStatus === 'error' ? (
                                    <Icon name="error" size={12} className="text-red-400" />
                                  ) : (
                                    <Icon name="auto_awesome" size={12} />
                                  )}
                                  <span>{briefing ? (expanded ? 'Hide' : 'Prep') : pStatus === 'loading' ? 'Getting ready...' : 'Prep'}</span>
                                </button>
                              )}
                              <button
                                onClick={() => handleCreateTask(ev, label, startTime)}
                                disabled={taskState === 'loading' || taskState === 'done'}
                                className="flex items-center gap-1 px-1.5 py-0.5 bg-slate-800 hover:bg-slate-700 rounded text-xs transition-colors disabled:opacity-50"
                                title="Create a prep needle"
                              >
                                {taskState === 'done' ? (
                                  <Icon name="check" size={12} className="text-green-400" />
                                ) : (
                                  <Icon name="add_task" size={12} />
                                )}
                                <span>Needle</span>
                              </button>
                              <button
                                onClick={() => deleteEvent(ev)}
                                className="flex items-center gap-1 px-1.5 py-0.5 text-slate-500 hover:text-red-400 rounded text-xs transition-colors"
                                title="Delete this event"
                                data-testid={`delete-event-${ev.id}`}
                              >
                                <Icon name="delete" size={12} />
                              </button>
                            </div>
                          </div>
                          {briefing && expanded && (
                            <div className="bg-violet-500/10 border border-violet-500/20 rounded-lg p-2.5 space-y-1.5">
                              <p className="text-[11px] text-slate-300 leading-relaxed whitespace-pre-wrap">{briefing}</p>
                              <div className="flex items-center gap-3">
                                <button
                                  onClick={() => navigator.clipboard.writeText(briefing)}
                                  className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
                                >
                                  <Icon name="content_copy" size={11} />
                                  Copy
                                </button>
                                <button
                                  onClick={() => handleCreateTasksFromBriefing(ev.id)}
                                  disabled={briefingTaskStatus[ev.id] === 'loading' || briefingTaskStatus[ev.id] === 'done'}
                                  className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
                                >
                                  {briefingTaskStatus[ev.id] === 'loading' ? (
                                    <><Icon name="progress_activity" size={11} className="animate-spin" /> Creating needles...</>
                                  ) : briefingTaskStatus[ev.id] === 'done' ? (
                                    <><Icon name="check_circle" size={11} className="text-green-400" /> {briefingTaskCount[ev.id] || 0} needles created</>
                                  ) : briefingTaskStatus[ev.id] === 'error' ? (
                                    <><Icon name="error" size={11} className="text-red-400" /> Failed</>
                                  ) : (
                                    <><Icon name="add_task" size={11} /> Create needles from briefing</>
                                  )}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {events.length === 0 && todayEvents.length === 0 && (
          <EmptyState
            icon="event_busy"
            title="No events in the next 7 days"
            description="Your calendar is clear. A good time to focus on your top needles."
          />
        )}
      </div>

      {undoDelete && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-800 border border-slate-700 text-sm text-slate-200 px-4 py-3 rounded-xl shadow-lg"
          data-testid="undo-delete-event-toast"
        >
          <span>Event deleted.</span>
          <button
            onClick={handleUndoDeleteEvent}
            className="font-medium text-blue-400 hover:text-blue-300"
            data-testid="undo-delete-event-button"
          >
            Undo
          </button>
        </div>
      )}
    </div>
  )
}
