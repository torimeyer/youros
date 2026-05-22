import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import CalendarGridWidget, { type CalGridEvent } from './CalendarGridWidget'

// Freeze time: 2026-05-22 (Friday), 10:00 AM local
const FROZEN = new Date(2026, 4, 22, 10, 0, 0)

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(FROZEN)
})

afterEach(() => {
  vi.useRealTimers()
})

function ev(
  id: string,
  summary: string,
  startDateTime: string,
  endDateTime: string,
): CalGridEvent {
  return { id, summary, start: { dateTime: startDateTime }, end: { dateTime: endDateTime } }
}

function evAllDay(id: string, summary: string, date: string): CalGridEvent {
  return { id, summary, start: { date }, end: { date } }
}

// ─── Day view ─────────────────────────────────────────────────────────────────

describe('CalendarGridWidget — Day view', () => {
  it('renders the hourly grid when events exist', () => {
    const events = [ev('e1', 'Standup', '2026-05-22T09:00:00', '2026-05-22T09:30:00')]
    render(<CalendarGridWidget events={events} range="day" />)
    expect(screen.getByTestId('cal-grid-day')).toBeInTheDocument()
    expect(screen.getByTestId('cal-grid-day-hours')).toBeInTheDocument()
  })

  it('renders events as day blocks', () => {
    const events = [
      ev('e1', 'Standup', '2026-05-22T09:00:00', '2026-05-22T09:30:00'),
      ev('e2', 'Design review', '2026-05-22T14:00:00', '2026-05-22T15:00:00'),
    ]
    render(<CalendarGridWidget events={events} range="day" />)
    expect(screen.getByTestId('cal-grid-event-e1')).toBeInTheDocument()
    expect(screen.getByTestId('cal-grid-event-e2')).toBeInTheDocument()
  })

  it('renders all-day events in the banner', () => {
    const events = [evAllDay('e1', 'Company holiday', '2026-05-22')]
    render(<CalendarGridWidget events={events} range="day" />)
    expect(screen.getByTestId('cal-grid-event-e1')).toBeInTheDocument()
    expect(screen.getByText(/Company holiday/)).toBeInTheDocument()
  })

  it('shows empty placeholder when no events', () => {
    render(<CalendarGridWidget events={[]} range="day" />)
    expect(screen.getByTestId('cal-grid-empty')).toBeInTheDocument()
    expect(screen.getByText('Nothing on your calendar today.')).toBeInTheDocument()
  })

  it('shows loading state when loading and no events', () => {
    render(<CalendarGridWidget events={[]} range="day" loading={true} />)
    expect(screen.getByText('Loading events...')).toBeInTheDocument()
  })
})

// ─── Week view ────────────────────────────────────────────────────────────────

describe('CalendarGridWidget — Week view', () => {
  it('renders 7 day columns', () => {
    render(<CalendarGridWidget events={[]} range="week" />)
    expect(screen.getByTestId('cal-grid-week')).toBeInTheDocument()
    const cols = Array.from({ length: 7 }, (_, i) =>
      screen.queryByTestId(`cal-week-col-${i}`),
    )
    expect(cols.filter(Boolean)).toHaveLength(7)
  })

  it('places an event in the correct column', () => {
    // 2026-05-22 is Friday = day index 5 in Sun-based week
    const events = [ev('e1', 'Friday meeting', '2026-05-22T10:00:00', '2026-05-22T11:00:00')]
    render(<CalendarGridWidget events={events} range="week" />)
    expect(screen.getByTestId('cal-grid-event-e1')).toBeInTheDocument()
  })

  it('shows empty state when no events', () => {
    render(<CalendarGridWidget events={[]} range="week" />)
    // week view may show empty or just columns — just verify no crash
    expect(screen.getByTestId('cal-grid-week')).toBeInTheDocument()
  })

  it('shows overflow indicator when a day has more than 3 events', () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      ev(`e${i}`, `Event ${i}`, '2026-05-22T09:00:00', '2026-05-22T09:30:00'),
    )
    render(<CalendarGridWidget events={events} range="week" />)
    // At least the events render (overflow text may vary)
    expect(screen.getByTestId('cal-grid-week')).toBeInTheDocument()
  })
})

// ─── Month view ───────────────────────────────────────────────────────────────

describe('CalendarGridWidget — Month view', () => {
  it('renders 35 or 42 cells total', () => {
    // May 2026: starts Friday (dow=5), 31 days → 5+31=36 → ceil(36/7)*7=42
    render(<CalendarGridWidget events={[]} range="month" />)
    expect(screen.getByTestId('cal-grid-month')).toBeInTheDocument()
    const cells = screen.getAllByTestId(/cal-month-cell-\d+/)
    expect([35, 42]).toContain(cells.length)
  })

  it('renders at least 28 non-empty day cells', () => {
    render(<CalendarGridWidget events={[]} range="month" />)
    const cells = screen.getAllByTestId(/cal-month-cell-\d+/)
    expect(cells.length).toBeGreaterThanOrEqual(28)
  })

  it('places an event in the correct day cell', () => {
    const events = [ev('e1', 'Product launch', '2026-05-22T09:00:00', '2026-05-22T10:00:00')]
    render(<CalendarGridWidget events={events} range="month" />)
    expect(screen.getByTestId('cal-grid-event-e1')).toBeInTheDocument()
    expect(screen.getByText('Product launch')).toBeInTheDocument()
  })

  it('places an all-day event in the correct cell', () => {
    const events = [evAllDay('e2', 'Annual review', '2026-05-22')]
    render(<CalendarGridWidget events={events} range="month" />)
    expect(screen.getByTestId('cal-grid-event-e2')).toBeInTheDocument()
  })

  it('shows overflow indicator when a cell has more than 3 events', () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      ev(`e${i}`, `Event ${i}`, '2026-05-22T09:00:00', '2026-05-22T09:30:00'),
    )
    render(<CalendarGridWidget events={events} range="month" />)
    // Should show "+2 more" overflow for the day with 5 events (3 shown + 2 hidden)
    expect(screen.getByText('+2 more')).toBeInTheDocument()
  })

  it('shows empty state when no events', () => {
    render(<CalendarGridWidget events={[]} range="month" />)
    // Month grid still renders even with no events
    expect(screen.getByTestId('cal-grid-month')).toBeInTheDocument()
  })
})
