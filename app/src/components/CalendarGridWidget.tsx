import { useMemo } from 'react'

export interface CalGridEvent {
  id: string
  summary?: string
  start: { dateTime?: string; date?: string }
  end: { dateTime?: string; date?: string }
  hangoutLink?: string
  colorId?: string
}

export type CalGridRange = 'day' | 'week' | 'month'

const DEFAULT_GCAL_COLOR_MAP: Record<string, string> = {
  '1': '#7986CB', '2': '#33B679', '3': '#8E24AA', '4': '#E67C73',
  '5': '#F6BF26', '6': '#F4511E', '7': '#039BE5', '8': '#616161',
  '9': '#3F51B5', '10': '#0B8043', '11': '#D50000',
}
const DEFAULT_GCAL_DEFAULT_COLOR = '#4285F4'

interface Props {
  events: CalGridEvent[]
  range: CalGridRange
  gcalColorMap?: Record<string, string>
  gcalDefaultColor?: string
  loading?: boolean
}

function evColor(ev: CalGridEvent, colorMap: Record<string, string>, defaultColor: string): string {
  return ev.colorId ? (colorMap[ev.colorId] ?? defaultColor) : defaultColor
}

function toLocalDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function getEventDateKey(ev: CalGridEvent): string {
  const s = ev.start.dateTime || ev.start.date || ''
  if (!s) return ''
  if (!ev.start.dateTime && ev.start.date) return s.substring(0, 10)
  const parsed = new Date(s)
  return isNaN(parsed.getTime()) ? s.substring(0, 10) : toLocalDateKey(parsed)
}

function isAllDay(ev: CalGridEvent): boolean {
  return !ev.start.dateTime && Boolean(ev.start.date)
}

function formatHour(h: number): string {
  if (h === 0 || h === 24) return '12am'
  if (h === 12) return '12pm'
  return h < 12 ? `${h}am` : `${h - 12}pm`
}

// --- Day view ---

const HOUR_HEIGHT = 32 // px per hour

interface LayoutEvent {
  ev: CalGridEvent
  startMin: number
  durationMin: number
  col: number
  totalCols: number
}

function buildDayLayout(events: CalGridEvent[], dayStartHour: number): LayoutEvent[] {
  const timed = events
    .filter(ev => Boolean(ev.start.dateTime))
    .sort((a, b) => (a.start.dateTime ?? '').localeCompare(b.start.dateTime ?? ''))

  const colEnds: number[] = []
  const result: LayoutEvent[] = []

  for (const ev of timed) {
    const sd = new Date(ev.start.dateTime!)
    const endStr = ev.end?.dateTime
    const ed = endStr ? new Date(endStr) : new Date(sd.getTime() + 30 * 60000)
    const startMin = (sd.getHours() - dayStartHour) * 60 + sd.getMinutes()
    const endMin = (ed.getHours() - dayStartHour) * 60 + ed.getMinutes()

    let col = colEnds.findIndex(e => e <= startMin)
    if (col === -1) col = colEnds.length
    colEnds[col] = Math.max(endMin, startMin + 30)

    result.push({ ev, startMin, durationMin: Math.max(30, endMin - startMin), col, totalCols: 0 })
  }

  const totalCols = Math.max(1, colEnds.length)
  for (const r of result) r.totalCols = totalCols
  return result
}

function DayView({
  events,
  gcalColorMap,
  gcalDefaultColor,
}: {
  events: CalGridEvent[]
  gcalColorMap: Record<string, string>
  gcalDefaultColor: string
}) {
  const allDayEvs = events.filter(isAllDay)
  const timedEvs = events.filter(ev => Boolean(ev.start.dateTime))

  // Dynamic hour range based on events
  let startH = 8, endH = 18
  for (const ev of timedEvs) {
    const d = new Date(ev.start.dateTime!)
    startH = Math.min(startH, d.getHours())
    const e = ev.end?.dateTime ? new Date(ev.end.dateTime) : new Date(d.getTime() + 3600000)
    endH = Math.max(endH, e.getHours() + (e.getMinutes() > 0 ? 1 : 0))
  }
  startH = Math.max(0, startH - 1)
  endH = Math.min(23, Math.max(endH + 1, startH + 4))
  const totalHours = endH - startH
  const totalHeight = totalHours * HOUR_HEIGHT

  const layout = buildDayLayout(timedEvs, startH)

  if (allDayEvs.length === 0 && timedEvs.length === 0) {
    return (
      <p className="text-sm text-slate-400" data-testid="cal-grid-empty">
        Nothing on your calendar today.
      </p>
    )
  }

  return (
    <div data-testid="cal-grid-day">
      {allDayEvs.map(ev => {
        const color = evColor(ev, gcalColorMap, gcalDefaultColor)
        return (
          <div
            key={ev.id}
            className="mb-1 flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium text-white truncate"
            style={{ backgroundColor: color + 'cc' }}
            data-testid={`cal-grid-event-${ev.id}`}
          >
            <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
            All day · {ev.summary || 'Untitled'}
          </div>
        )
      })}
      <div className="relative ml-9" style={{ height: totalHeight }} data-testid="cal-grid-day-hours">
        {Array.from({ length: totalHours + 1 }, (_, i) => (
          <div
            key={i}
            className="absolute left-0 right-0 border-t border-slate-800/60"
            style={{ top: i * HOUR_HEIGHT }}
            data-testid={`cal-day-hour-${startH + i}`}
          >
            <span className="absolute -left-9 -top-2.5 text-[9px] text-slate-600 w-8 text-right leading-none">
              {formatHour(startH + i)}
            </span>
          </div>
        ))}
        {layout.map(({ ev, startMin, durationMin, col, totalCols }) => {
          const color = evColor(ev, gcalColorMap, gcalDefaultColor)
          const widthPct = 100 / totalCols
          const leftPct = (col / totalCols) * 100
          const top = (startMin / 60) * HOUR_HEIGHT
          const height = Math.max(18, (durationMin / 60) * HOUR_HEIGHT)
          return (
            <div
              key={ev.id}
              className="absolute rounded overflow-hidden text-white"
              style={{
                top,
                height,
                left: `${leftPct}%`,
                width: `calc(${widthPct}% - 2px)`,
                backgroundColor: color + 'cc',
                borderLeft: `2px solid ${color}`,
              }}
              data-testid={`cal-grid-event-${ev.id}`}
            >
              <span aria-hidden="true" className="sr-only" style={{ backgroundColor: color }} />
              <p className="text-[9px] font-semibold leading-tight px-1 py-0.5 truncate">
                {ev.summary || 'Untitled'}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --- Week view ---

const DAY_SHORT = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']
const WEEK_MAX_SHOW = 3

function WeekView({
  events,
  gcalColorMap,
  gcalDefaultColor,
  today,
}: {
  events: CalGridEvent[]
  gcalColorMap: Record<string, string>
  gcalDefaultColor: string
  today: Date
}) {
  const todayKey = toLocalDateKey(today)
  const weekStart = new Date(today)
  weekStart.setDate(today.getDate() - today.getDay())
  weekStart.setHours(0, 0, 0, 0)

  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart)
    d.setDate(weekStart.getDate() + i)
    return d
  })

  const byDay: Record<string, CalGridEvent[]> = {}
  for (const ev of events) {
    const key = getEventDateKey(ev)
    if (key) {
      if (!byDay[key]) byDay[key] = []
      byDay[key].push(ev)
    }
  }

  return (
    <div className="grid grid-cols-7 gap-px" data-testid="cal-grid-week">
      {days.map((d, i) => {
        const key = toLocalDateKey(d)
        const dayEvs = byDay[key] ?? []
        const isToday = key === todayKey
        const shown = dayEvs.slice(0, WEEK_MAX_SHOW)
        const extra = dayEvs.length - WEEK_MAX_SHOW

        return (
          <div
            key={i}
            className={`flex flex-col min-h-[80px] p-1 rounded ${
              isToday
                ? 'bg-blue-500/10 ring-1 ring-blue-500/40'
                : 'bg-slate-900/30'
            }`}
            data-testid={`cal-week-col-${i}`}
          >
            <p className={`text-[9px] font-medium uppercase leading-none mb-0.5 ${isToday ? 'text-blue-300' : 'text-slate-500'}`}>
              {DAY_SHORT[i]}
            </p>
            <p className={`text-xs font-bold leading-none mb-1 ${isToday ? 'text-blue-200' : 'text-slate-300'}`}>
              {d.getDate()}
            </p>
            <div className="space-y-0.5 flex-1 min-w-0">
              {shown.map(ev => {
                const color = evColor(ev, gcalColorMap, gcalDefaultColor)
                return (
                  <div
                    key={ev.id}
                    className="flex items-center gap-0.5 rounded px-0.5 py-px text-[8px] text-white truncate"
                    style={{ backgroundColor: color + 'bb' }}
                    data-testid={`cal-grid-event-${ev.id}`}
                  >
                    <span
                      aria-hidden="true"
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ backgroundColor: color }}
                    />
                    <span className="truncate leading-tight">{ev.summary || 'Untitled'}</span>
                  </div>
                )
              })}
              {extra > 0 && (
                <p className="text-[8px] text-slate-500 leading-none">+{extra} more</p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// --- Month view ---

const DAY_LETTERS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']
const MONTH_MAX_SHOW = 3

function MonthView({
  events,
  gcalColorMap,
  gcalDefaultColor,
  today,
}: {
  events: CalGridEvent[]
  gcalColorMap: Record<string, string>
  gcalDefaultColor: string
  today: Date
}) {
  const year = today.getFullYear()
  const month = today.getMonth()
  const todayKey = toLocalDateKey(today)

  const firstOfMonth = new Date(year, month, 1)
  const startDow = firstOfMonth.getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const totalCells = startDow + daysInMonth <= 35 ? 35 : 42

  const byDay: Record<string, CalGridEvent[]> = {}
  for (const ev of events) {
    const key = getEventDateKey(ev)
    if (key) {
      if (!byDay[key]) byDay[key] = []
      byDay[key].push(ev)
    }
  }

  return (
    <div data-testid="cal-grid-month">
      <div className="grid grid-cols-7 mb-px">
        {DAY_LETTERS.map((n, i) => (
          <div key={i} className="text-center text-[9px] font-medium text-slate-600 py-0.5">
            {n}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-px">
        {Array.from({ length: totalCells }, (_, i) => {
          const dayNum = i - startDow + 1
          const isCurrentMonth = dayNum >= 1 && dayNum <= daysInMonth
          const d = isCurrentMonth ? new Date(year, month, dayNum) : null
          const key = d ? toLocalDateKey(d) : ''
          const dayEvs = key ? (byDay[key] ?? []) : []
          const isToday = key === todayKey
          const shown = dayEvs.slice(0, MONTH_MAX_SHOW)
          const extra = dayEvs.length - MONTH_MAX_SHOW
          return (
            <div
              key={i}
              className={`min-h-[40px] p-px text-[8px] rounded ${
                !isCurrentMonth
                  ? 'opacity-0 pointer-events-none'
                  : isToday
                  ? 'bg-blue-500/10 ring-1 ring-blue-500/40'
                  : 'bg-slate-900/20'
              }`}
              data-testid={`cal-month-cell-${i}`}
            >
              {isCurrentMonth && (
                <>
                  <p className={`text-[9px] font-medium leading-tight mb-px ${isToday ? 'text-blue-300 font-bold' : 'text-slate-400'}`}>
                    {dayNum}
                  </p>
                  {shown.map(ev => {
                    const color = evColor(ev, gcalColorMap, gcalDefaultColor)
                    return (
                      <div
                        key={ev.id}
                        className="flex items-center gap-px rounded px-px text-white truncate mb-px leading-tight"
                        style={{ backgroundColor: color + 'bb' }}
                        data-testid={`cal-grid-event-${ev.id}`}
                      >
                        <span
                          aria-hidden="true"
                          className="w-1 h-1 rounded-full shrink-0 flex-none"
                          style={{ backgroundColor: color }}
                        />
                        <span className="truncate">{ev.summary || '·'}</span>
                      </div>
                    )
                  })}
                  {extra > 0 && (
                    <p className="text-slate-500 leading-tight">+{extra}</p>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --- Main export ---

export default function CalendarGridWidget({
  events,
  range,
  gcalColorMap = DEFAULT_GCAL_COLOR_MAP,
  gcalDefaultColor = DEFAULT_GCAL_DEFAULT_COLOR,
  loading,
}: Props) {
  const today = useMemo(() => new Date(), [])

  if (loading && events.length === 0) {
    return <p className="text-sm text-slate-400">Loading events...</p>
  }

  if (range === 'day') {
    return (
      <DayView
        events={events}
        gcalColorMap={gcalColorMap}
        gcalDefaultColor={gcalDefaultColor}
      />
    )
  }
  if (range === 'week') {
    return (
      <WeekView
        events={events}
        gcalColorMap={gcalColorMap}
        gcalDefaultColor={gcalDefaultColor}
        today={today}
      />
    )
  }
  return (
    <MonthView
      events={events}
      gcalColorMap={gcalColorMap}
      gcalDefaultColor={gcalDefaultColor}
      today={today}
    />
  )
}
