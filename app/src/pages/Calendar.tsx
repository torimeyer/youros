import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { api } from '../lib/api'

interface CalendarEvent {
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

function formatDuration(start?: string, end?: string): string {
  if (!start || !end) return ''
  try {
    const diffMs = new Date(end).getTime() - new Date(start).getTime()
    const mins = Math.round(diffMs / 60000)
    if (mins < 60) return `${mins}m`
    const h = Math.floor(mins / 60)
    const m = mins % 60
    return m > 0 ? `${h}h ${m}m` : `${h}h`
  } catch {
    return ''
  }
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

function getEventDate(ev: CalendarEvent): string {
  return (ev.start?.dateTime || ev.start?.date || '').substring(0, 10)
}


export default function Calendar() {
  const [searchParams] = useSearchParams()
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [lastSynced, setLastSynced] = useState<Date | null>(null)
  const [createTaskStatus, setCreateTaskStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [prepStatus, setPrepStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [prepBriefings, setPrepBriefings] = useState<Record<string, string>>({})
  const [expandedPrep, setExpandedPrep] = useState<Record<string, boolean>>({})
  const [apiNotEnabled, setApiNotEnabled] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

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
      setEvents(res.events || [])
      setLastSynced(new Date())
      setApiNotEnabled(false)
    } catch (err: unknown) {
      setEvents([])
      const detail = (err as { response?: { data?: { detail?: { api_not_enabled?: boolean } } } })?.response?.data?.detail
      if (detail?.api_not_enabled) setApiNotEnabled(true)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    ;(async () => {
      try {
        const status = await api.get<AuthStatus>('/calendar/auth/status')
        setAuthStatus(status)
        if (status.authenticated && !status.needs_reauth) {
          await fetchEvents()
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

  const handleConnect = async () => {
    setConnectError(null)
    try {
      const res = await api.get<ConnectAuthUrl>('/drive/auth/url/calendar')
      window.location.href = res.url
    } catch {
      setConnectError(
        'Could not get the sign-in link. Make sure the myOS backend is running and your Google credentials file is saved at ~/.myos/google_credentials.json.'
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

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  // Group events by day
  const todayStr = new Date().toISOString().substring(0, 10)
  const todayEvents = events.filter((ev) => getEventDate(ev) === todayStr)
  const upcomingGroups: Record<string, CalendarEvent[]> = {}
  events.forEach((ev) => {
    const d = getEventDate(ev)
    if (d !== todayStr) {
      if (!upcomingGroups[d]) upcomingGroups[d] = []
      upcomingGroups[d].push(ev)
    }
  })

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-20 p-8 flex items-center gap-2 text-slate-400">
          <Icon name="progress_activity" size={20} className="animate-spin" />
          Loading...
        </div>
      </div>
    )
  }

  if (!authStatus?.authenticated || authStatus.needs_reauth) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-20 p-8 max-w-md mx-auto">
          <div className="bg-slate-900/40 border border-slate-800 p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center mb-4">
              <Icon name="calendar_month" className="text-blue-400" size={24} />
            </div>
            {authStatus?.needs_reauth ? (
              <>
                <h2 className="text-xl font-semibold mb-2">Calendar access needs to be updated</h2>
                <p className="text-slate-400 mb-6">
                  Reconnect your Google account to give myOS permission to read your calendar.
                  This uses the same account you already connected for Drive.
                </p>
                <button
                  onClick={handleConnect}
                  className="w-full py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
                >
                  Reconnect
                </button>
              </>
            ) : (
              <>
                <h2 className="text-xl font-semibold mb-2">Connect Google Calendar</h2>
                <p className="text-slate-400 mb-6">
                  See your upcoming meetings and create tasks from events.
                  This uses the same Google account as Drive, so no extra credentials are needed.
                </p>
                <button
                  onClick={handleConnect}
                  className="w-full py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
                >
                  Connect Google account
                </button>
              </>
            )}
            {connectError && (
              <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
                {connectError}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (apiNotEnabled) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Calendar" />
        <div className="pt-20 p-8 max-w-md mx-auto">
          <div className="bg-slate-900/40 border border-amber-800/40 p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-amber-500/20 flex items-center justify-center mb-4">
              <Icon name="warning" className="text-amber-400" size={24} />
            </div>
            <h2 className="text-xl font-semibold mb-2">Calendar API not enabled</h2>
            <p className="text-slate-400 mb-4">
              Your Google Cloud project has Google Calendar API disabled. You need to enable it once. It only takes a minute.
            </p>
            <a
              href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
              target="_blank"
              rel="noreferrer"
              className="w-full block text-center py-3 mb-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
            >
              Enable Calendar API in Google Cloud
            </a>
            <p className="text-xs text-slate-500 mb-4">
              After clicking Enable on Google's page, wait 1-2 minutes for the change to propagate, then come back and click Retry.
            </p>
            <button
              onClick={() => { setApiNotEnabled(false); fetchEvents() }}
              className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Calendar" />
      <div className="pt-20 p-8">
        {/* Header row */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Calendar</h1>
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
                const duration = formatDuration(ev.start?.dateTime, ev.end?.dateTime)
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
                    >
                      <div className="min-w-[72px] text-right shrink-0">
                        <p className="text-sm font-medium text-slate-200">{startTime}</p>
                        {duration && <p className="text-xs text-slate-500">{duration}</p>}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-sm truncate">{ev.summary || 'Untitled'}</p>
                          {inProgress && (
                            <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded-full shrink-0">
                              Now
                            </span>
                          )}
                        </div>
                        {ev.location && (
                          <p className="text-xs text-slate-400 truncate mt-0.5">{ev.location}</p>
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
                          title="Create a prep task"
                        >
                          {taskState === 'done' ? (
                            <><Icon name="check" size={14} className="text-green-400" /> Done</>
                          ) : taskState === 'error' ? (
                            <><Icon name="error" size={14} className="text-red-400" /> Error</>
                          ) : (
                            <><Icon name="add_task" size={14} /> Task</>
                          )}
                        </button>
                      </div>
                    </div>
                    {briefing && expanded && (
                      <div className="ml-[88px] bg-violet-500/10 border border-violet-500/20 rounded-xl p-3 space-y-2">
                        <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">{briefing}</p>
                        <button
                          onClick={() => navigator.clipboard.writeText(briefing)}
                          className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
                        >
                          <Icon name="content_copy" size={12} />
                          Copy
                        </button>
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
                      const duration = formatDuration(ev.start?.dateTime, ev.end?.dateTime)
                      const taskState = createTaskStatus[ev.id]
                      const within24h = isWithin24Hours(ev)
                      const pStatus = prepStatus[ev.id]
                      const briefing = prepBriefings[ev.id]
                      const expanded = expandedPrep[ev.id]
                      return (
                        <div key={ev.id} className="space-y-1.5">
                          <div
                            className="flex items-start gap-3 p-2.5 rounded-lg border border-slate-800/50 bg-slate-900/20"
                          >
                            <div className="min-w-[64px] text-right shrink-0">
                              <p className="text-xs font-medium text-slate-300">{startTime}</p>
                              {duration && <p className="text-[10px] text-slate-500">{duration}</p>}
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm truncate">{ev.summary || 'Untitled'}</p>
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
                                title="Create a prep task"
                              >
                                {taskState === 'done' ? (
                                  <Icon name="check" size={12} className="text-green-400" />
                                ) : (
                                  <Icon name="add_task" size={12} />
                                )}
                                <span>Task</span>
                              </button>
                            </div>
                          </div>
                          {briefing && expanded && (
                            <div className="ml-[79px] bg-violet-500/10 border border-violet-500/20 rounded-lg p-2.5 space-y-1.5">
                              <p className="text-[11px] text-slate-300 leading-relaxed whitespace-pre-wrap">{briefing}</p>
                              <button
                                onClick={() => navigator.clipboard.writeText(briefing)}
                                className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
                              >
                                <Icon name="content_copy" size={11} />
                                Copy
                              </button>
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
          <div className="text-center py-12 text-slate-500">
            <Icon name="event_busy" size={40} className="mb-3 mx-auto opacity-40" />
            <p className="text-sm text-slate-400 mb-1">No events in the next 7 days.</p>
            <p className="text-xs text-slate-600">Your calendar is clear. A good time to focus on your top tasks.</p>
          </div>
        )}
      </div>
    </div>
  )
}
