import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Calendar from './Calendar'
import { ApiError } from '../lib/api'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  }
})

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const AUTHENTICATED = {
  authenticated: true,
  needs_reauth: false,
  email: 'tori@example.com',
}

function renderCalendar() {
  return render(
    <MemoryRouter>
      <Calendar />
    </MemoryRouter>
  )
}

function makeApiNotEnabledError(): ApiError {
  // Match the shape that ApiError builds for FastAPI detail responses.
  // routers raise HTTPException(status_code=403, detail={"api_not_enabled": true, ...})
  // which our request() helper converts into an ApiError whose
  // .response.data.detail.api_not_enabled === true.
  const body = JSON.stringify({
    detail: {
      api_not_enabled: true,
      message: 'Calendar API is not enabled in this Google Cloud project.',
    },
  })
  return new ApiError(403, body)
}

describe('Calendar page api_not_enabled screen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the Enable Calendar API link when api_not_enabled is true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) {
        return Promise.resolve(AUTHENTICATED)
      }
      if (path.includes('/calendar/events')) {
        return Promise.reject(makeApiNotEnabledError())
      }
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByText('Calendar API not enabled')).toBeInTheDocument()
    })

    const link = screen.getByRole('link', { name: /Enable Calendar API in Google Cloud/i })
    expect(link).toBeInTheDocument()
  })

  it('points the link to the correct Google Cloud Console URL', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.reject(makeApiNotEnabledError())
      return Promise.resolve({})
    })

    renderCalendar()

    const link = await screen.findByRole('link', { name: /Enable Calendar API in Google Cloud/i })
    expect(link.getAttribute('href')).toBe(
      'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com'
    )
  })

  it('opens the link in a new tab with rel noreferrer', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.reject(makeApiNotEnabledError())
      return Promise.resolve({})
    })

    renderCalendar()

    const link = await screen.findByRole('link', { name: /Enable Calendar API in Google Cloud/i })
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  })

  it('Retry button re-fetches events and clears the not-enabled state on success', async () => {
    let eventsCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) {
        eventsCalls += 1
        if (eventsCalls === 1) {
          return Promise.reject(makeApiNotEnabledError())
        }
        return Promise.resolve({ events: [] })
      }
      return Promise.resolve({})
    })

    renderCalendar()

    const retryButton = await screen.findByRole('button', { name: /Retry/i })
    expect(retryButton).toBeInTheDocument()

    fireEvent.click(retryButton)

    // After Retry succeeds the not-enabled screen should disappear.
    await waitFor(() => {
      expect(screen.queryByText('Calendar API not enabled')).not.toBeInTheDocument()
    })
    expect(eventsCalls).toBeGreaterThanOrEqual(2)
  })
})

describe('Calendar ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows ConnectCard with blue accent when not authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) {
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null })
      }
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // The icon container carries the blue accent color via inline style
    // jsdom converts #3b82f6 to rgb(59, 130, 246) in the DOM
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/59, 130, 246/)
  })

  it('shows ConnectCard with reauth title when needs_reauth is true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) {
        return Promise.resolve({ authenticated: true, needs_reauth: true, email: null })
      }
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.getByText(/Calendar access needs to be updated/i)).toBeInTheDocument()
  })
})

describe('Calendar empty-cache recovery (demo bug fix)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders events when the API returns some', async () => {
    const fakeEvents = [
      {
        id: 'ev-1',
        summary: 'Fox - soccer game',
        start: { dateTime: '2026-04-16T19:30:00-05:00' },
        end: { dateTime: '2026-04-16T20:30:00-05:00' },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
  })

  it('auto-syncs when the first events fetch comes back empty, then renders the new events', async () => {
    let getCalls = 0
    const realEvents = [
      {
        id: 'ev-2',
        summary: 'Pepper - gymnastics',
        start: { dateTime: '2026-04-21T16:45:00-05:00' },
        end: { dateTime: '2026-04-21T17:45:00-05:00' },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) {
        getCalls += 1
        // First call: empty (simulates stale empty cache).
        // Second call: real events (after the sync clears the cache).
        return Promise.resolve({ events: getCalls === 1 ? [] : realEvents })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/calendar/sync')) return Promise.resolve({ ok: true, count: 1 })
      return Promise.resolve({})
    })

    renderCalendar()

    // After the auto-sync fallback the real event should land on screen.
    await waitFor(() => {
      expect(screen.getByText('Pepper - gymnastics')).toBeInTheDocument()
    })

    // Sanity check: the sync was actually called.
    expect(mockedApiPost).toHaveBeenCalledWith('/calendar/sync', {})
  })
})

describe('Calendar localStorage cache (faster return visits)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.calendarCache.v1')
  })

  afterEach(() => {
    window.localStorage.removeItem('myos.calendarCache.v1')
  })

  it('paints events from localStorage immediately before the network responds', async () => {
    const cached = [
      {
        id: 'ev-cached',
        summary: 'Standup from cache',
        start: { dateTime: '2026-04-21T09:00:00-05:00' },
        end: { dateTime: '2026-04-21T09:30:00-05:00' },
      },
    ]
    window.localStorage.setItem('myos.calendarCache.v1', JSON.stringify(cached))

    // Network hangs so we can prove the first paint came from cache.
    let resolveStatus: (v: unknown) => void = () => {}
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) {
        return new Promise((resolve) => {
          resolveStatus = resolve
        })
      }
      if (path.includes('/calendar/events')) return new Promise(() => {})
      return Promise.resolve({})
    })

    renderCalendar()

    // Row is painted from cache before the status response lands.
    await waitFor(() => {
      expect(screen.getByText('Standup from cache')).toBeInTheDocument()
    })

    resolveStatus(AUTHENTICATED)
  })

  it('writes fresh events to localStorage so the next visit paints instantly', async () => {
    const freshEvents = [
      {
        id: 'ev-fresh',
        summary: 'New standup',
        start: { dateTime: '2026-04-21T10:00:00-05:00' },
        end: { dateTime: '2026-04-21T10:30:00-05:00' },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: freshEvents })
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByText('New standup')).toBeInTheDocument()
    })

    // The freshly fetched events should now be in localStorage.
    const saved = window.localStorage.getItem('myos.calendarCache.v1')
    expect(saved).not.toBeNull()
    expect(saved && JSON.parse(saved)[0].id).toBe('ev-fresh')
  })
})

describe('Calendar connect error', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows inline error instead of browser alert when connect fails', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) {
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null })
      }
      if (path.includes('/drive/auth/url/calendar')) {
        return Promise.reject(new Error('Failed to fetch'))
      }
      return Promise.resolve({})
    })

    renderCalendar()

    const connectButton = await screen.findByRole('button', { name: /Connect Google account/i })
    fireEvent.click(connectButton)

    await waitFor(() => {
      expect(screen.getByText(/Could not get the sign-in link/i)).toBeInTheDocument()
    })
  })
})

describe('Calendar day grouping helpers (needle 282)', () => {
  // Regression for the bug where a late-evening session in PDT would
  // read ``new Date().toISOString()`` as the NEXT UTC day and then
  // compare that UTC day string against event.start.dateTime, causing
  // tomorrow's events to land under the Today strip. The fix uses a
  // local-calendar-day key for both sides of the comparison.
  //
  // Tests the helper functions directly. A full Calendar render test
  // would need to freeze the wall clock with useFakeTimers, which
  // breaks the waitFor polling that render-based tests rely on.

  it('toLocalDateKey formats local date as YYYY-MM-DD', async () => {
    const { toLocalDateKey } = await import('./Calendar')
    const d = new Date(2026, 3, 10)  // April 10, 2026 local
    expect(toLocalDateKey(d)).toBe('2026-04-10')
  })

  it('toLocalDateKey pads month and day', async () => {
    const { toLocalDateKey } = await import('./Calendar')
    const d = new Date(2026, 0, 1)  // January 1, 2026 local
    expect(toLocalDateKey(d)).toBe('2026-01-01')
  })

  it('getEventDate returns local day for a dateTime event crossing the UTC boundary', async () => {
    const { getEventDate, toLocalDateKey } = await import('./Calendar')
    // The event starts at 11:30 AM PDT on April 11.
    // Parsed into a Date, the local components are April 11 PDT.
    const ev = {
      id: 'soccer',
      start: { dateTime: '2026-04-11T11:30:00-07:00' },
      end:   { dateTime: '2026-04-11T12:45:00-07:00' },
    }
    const parsed = new Date(ev.start.dateTime)
    // The helper must return the local key for the parsed date, which
    // matches what the grouping key uses. Before the fix getEventDate
    // returned the literal ISO prefix which could drift across tz.
    expect(getEventDate(ev)).toBe(toLocalDateKey(parsed))
  })

  it('getEventDate trusts all-day event date verbatim', async () => {
    const { getEventDate } = await import('./Calendar')
    // All-day events use ``date`` (no time component). Must be
    // returned literal so an all-day April 11 event stays on April
    // 11 regardless of the viewer's tz offset.
    const ev = {
      id: 'holiday',
      start: { date: '2026-04-11' },
      end:   { date: '2026-04-12' },
    }
    expect(getEventDate(ev)).toBe('2026-04-11')
  })

  it('getEventDate returns empty string for an event with no start', async () => {
    const { getEventDate } = await import('./Calendar')
    const ev = { id: 'broken', start: {}, end: {} }
    expect(getEventDate(ev)).toBe('')
  })

  it('toLocalDateKey always matches the calendar day getFullYear/getMonth/getDate describe', async () => {
    // Run on a handful of sample dates so the helper is sanity-checked
    // in whatever tz the test host happens to be in. This avoids
    // hardcoding a tz-specific assertion that would only pass in PDT.
    const { toLocalDateKey } = await import('./Calendar')
    const samples = [
      new Date(2026, 0, 1, 0, 0, 0),      // Jan 1
      new Date(2026, 3, 10, 23, 59, 59),  // Apr 10 23:59:59
      new Date(2026, 11, 31, 12, 0, 0),   // Dec 31 noon
    ]
    for (const d of samples) {
      const expected = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      expect(toLocalDateKey(d)).toBe(expected)
    }
  })
})

describe('Calendar countdown subtitle (formatCountdown helper)', () => {
  // These are pure-function tests. The render-based tests below
  // exercise the integration (mount the Today strip with a fake event
  // and read the rendered countdown text from the DOM).

  it('returns empty string when start is missing', async () => {
    const { formatCountdown } = await import('./Calendar')
    expect(formatCountdown(undefined, undefined, Date.now())).toBe('')
  })

  it('returns empty string when the event has already ended', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T12:00:00Z').getTime()
    // Event was 9am to 10am, now is noon.
    const start = '2026-04-15T09:00:00Z'
    const end = '2026-04-15T10:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('')
  })

  it('returns "Happening now" when the event is in progress', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T12:15:00Z').getTime()
    // Event from 12:00 to 13:00.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Happening now')
  })

  it('returns "Starts soon" under 5 minutes away', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T11:58:00Z').getTime()
    // 2 minutes out.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Starts soon')
  })

  it('returns "Starts in X minutes" in the 5 to 30 minute window', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T11:48:00Z').getTime()
    // 12 minutes out.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Starts in 12 minutes')
  })

  it('returns "Coming up in 1 hour" in the 31 to 60 minute window', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T11:15:00Z').getTime()
    // 45 minutes out.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Coming up in 1 hour')
  })

  it('returns "Coming up in 2 hours" when more than an hour away', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T10:00:00Z').getTime()
    // 2 hours out.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Coming up in 2 hours')
  })

  it('rounds 1h 20m down to "Coming up in 1 hour"', async () => {
    const { formatCountdown } = await import('./Calendar')
    const now = new Date('2026-04-15T10:40:00Z').getTime()
    // 1h 20m out.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    expect(formatCountdown(start, end, now)).toBe('Coming up in 1 hour')
  })

  it('never renders a bare "1h" string anywhere', async () => {
    const { formatCountdown } = await import('./Calendar')
    // Sweep across every minute of a 3 hour window and make sure the
    // bare "1h" short form is never returned. Guards against a
    // regression where someone restores the old duration-style label.
    const start = '2026-04-15T12:00:00Z'
    const end = '2026-04-15T13:00:00Z'
    const baseNow = new Date('2026-04-15T09:00:00Z').getTime()
    for (let offset = 0; offset < 180; offset += 1) {
      const now = baseNow + offset * 60000
      const label = formatCountdown(start, end, now)
      expect(label).not.toBe('1h')
      expect(label).not.toMatch(/^\d+h$/)
    }
  })
})

describe('Calendar countdown subtitle (rendered)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // ``shouldAdvanceTime: true`` keeps timers ticking in real wall
    // time so Testing Library's ``waitFor`` polling still completes,
    // while ``setSystemTime`` lets each test pin the calendar to a
    // known moment. Without this flag, ``waitFor`` would hang
    // forever because its internal interval never fires.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function renderWithEventAtDeltaMinutes(deltaMinutes: number, durationMinutes = 60) {
    // Freeze the wall clock at a known moment, then build an event
    // whose start is ``deltaMinutes`` from now. Negative means the
    // event already started.
    const fixedNow = new Date('2026-04-15T19:30:00-05:00')
    vi.setSystemTime(fixedNow)
    const start = new Date(fixedNow.getTime() + deltaMinutes * 60000)
    const end = new Date(start.getTime() + durationMinutes * 60000)
    const fakeEvents = [
      {
        id: 'ev-countdown',
        summary: 'Fox - soccer game',
        start: { dateTime: start.toISOString() },
        end: { dateTime: end.toISOString() },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
    return screen.getByTestId('countdown-ev-countdown') as HTMLElement | null
  }

  async function renderWithEventAtDeltaMinutesExpectNoSubtitle(deltaMinutes: number, durationMinutes: number) {
    // Same as above but the caller is testing the "hide subtitle"
    // case, so we assert the element is NOT present instead of
    // returning it.
    const fixedNow = new Date('2026-04-15T19:30:00-05:00')
    vi.setSystemTime(fixedNow)
    const start = new Date(fixedNow.getTime() + deltaMinutes * 60000)
    const end = new Date(start.getTime() + durationMinutes * 60000)
    const fakeEvents = [
      {
        id: 'ev-countdown',
        summary: 'Fox - soccer game',
        start: { dateTime: start.toISOString() },
        end: { dateTime: end.toISOString() },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
  }

  it('test_calendar_card_shows_coming_up_label_within_1h_window', async () => {
    // Event is 45 minutes away. Should read "Coming up in 1 hour".
    const el = await renderWithEventAtDeltaMinutes(45)
    expect(el).not.toBeNull()
    expect(el!.textContent).toBe('Coming up in 1 hour')
  })

  it('test_calendar_card_shows_minutes_under_30m', async () => {
    // Event is 15 minutes away. Should read "Starts in 15 minutes".
    const el = await renderWithEventAtDeltaMinutes(15)
    expect(el).not.toBeNull()
    expect(el!.textContent).toBe('Starts in 15 minutes')
  })

  it('test_calendar_card_shows_happening_now_during_event', async () => {
    // Event started 10 minutes ago and still has 50 minutes left.
    const el = await renderWithEventAtDeltaMinutes(-10, 60)
    expect(el).not.toBeNull()
    expect(el!.textContent).toBe('Happening now')
  })

  it('test_calendar_card_hides_subtitle_after_event_ends', async () => {
    // Event ended 30 minutes ago. Subtitle should be hidden entirely.
    await renderWithEventAtDeltaMinutesExpectNoSubtitle(-90, 30)
    expect(screen.queryByTestId('countdown-ev-countdown')).toBeNull()
  })
})

describe('Calendar countdown badge (pretty pill with clock icon)', () => {
  // These tests cover the visual badge refactor: the countdown subtitle
  // must render as a rounded pill with a clock icon (or pulsing dot
  // when the event is in progress) and pick an accent color based on
  // how close the event is. The plain gray "Coming up in 1 hour" line
  // is replaced by a styled chip that matches the rest of myOS.

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function renderWithEventAtDeltaMinutes(deltaMinutes: number, durationMinutes = 60) {
    const fixedNow = new Date('2026-04-15T19:30:00-05:00')
    vi.setSystemTime(fixedNow)
    const start = new Date(fixedNow.getTime() + deltaMinutes * 60000)
    const end = new Date(start.getTime() + durationMinutes * 60000)
    const fakeEvents = [
      {
        id: 'ev-badge',
        summary: 'Fox - soccer game',
        start: { dateTime: start.toISOString() },
        end: { dateTime: end.toISOString() },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
  }

  it('test_calendar_countdown_renders_as_badge_with_clock_icon', async () => {
    // 45 minutes out. The subtitle must render as a pill, not a plain
    // paragraph, and must include a clock icon on its left.
    await renderWithEventAtDeltaMinutes(45)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge).toBeInTheDocument()
    // Pill shape comes from rounded-full + inline-flex.
    expect(badge.className).toMatch(/rounded-full/)
    expect(badge.className).toMatch(/inline-flex/)
    // Clock icon lives inside the badge for the non-live states.
    const clock = screen.getByTestId('countdown-clock-icon')
    expect(clock).toBeInTheDocument()
    expect(badge.contains(clock)).toBe(true)
  })

  it('test_calendar_countdown_red_badge_when_under_5m', async () => {
    // 3 minutes out. Must paint the red accent so it reads as urgent.
    await renderWithEventAtDeltaMinutes(3)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge.className).toMatch(/bg-red-500\/20/)
    expect(badge.className).toMatch(/text-red-400/)
    expect(badge.className).toMatch(/border-red-500\/40/)
  })

  it('test_calendar_countdown_amber_badge_in_5_to_30m_window', async () => {
    // 15 minutes out. Amber accent, meaning "start wrapping up".
    await renderWithEventAtDeltaMinutes(15)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge.className).toMatch(/bg-amber-500\/20/)
    expect(badge.className).toMatch(/text-amber-400/)
    expect(badge.className).toMatch(/border-amber-500\/40/)
  })

  it('test_calendar_countdown_blue_badge_in_30_to_60m_window', async () => {
    // 45 minutes out. Blue accent for the "Coming up in 1 hour" state.
    await renderWithEventAtDeltaMinutes(45)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge.className).toMatch(/bg-blue-500\/20/)
    expect(badge.className).toMatch(/text-blue-400/)
    expect(badge.className).toMatch(/border-blue-500\/40/)
  })

  it('test_calendar_countdown_slate_badge_when_more_than_1h_out', async () => {
    // 3 hours out. Slate accent: informational, not urgent.
    await renderWithEventAtDeltaMinutes(180)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge.className).toMatch(/bg-slate-500\/20/)
    expect(badge.className).toMatch(/text-slate-400/)
    expect(badge.className).toMatch(/border-slate-500\/40/)
  })

  it('test_calendar_countdown_green_badge_with_pulse_when_happening_now', async () => {
    // Event started 10 minutes ago, still 50 minutes left. Green
    // accent plus a pulsing dot that replaces the clock icon.
    await renderWithEventAtDeltaMinutes(-10, 60)
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(badge.className).toMatch(/bg-green-500\/20/)
    expect(badge.className).toMatch(/text-green-400/)
    expect(badge.className).toMatch(/border-green-500\/40/)
    // Pulsing dot present, clock icon absent.
    const pulse = screen.getByTestId('countdown-pulse-ev-badge')
    expect(pulse).toBeInTheDocument()
    expect(pulse.className).toMatch(/animate-pulse/)
    expect(screen.queryByTestId('countdown-clock-icon')).toBeNull()
  })

  it('test_calendar_countdown_badge_hidden_after_event_ends', async () => {
    // Event ended 30 minutes ago. Badge must not render at all.
    const fixedNow = new Date('2026-04-15T19:30:00-05:00')
    vi.setSystemTime(fixedNow)
    const start = new Date(fixedNow.getTime() - 90 * 60000)
    const end = new Date(start.getTime() + 30 * 60000)
    const fakeEvents = [
      {
        id: 'ev-badge',
        summary: 'Fox - soccer game',
        start: { dateTime: start.toISOString() },
        end: { dateTime: end.toISOString() },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('countdown-badge-ev-badge')).toBeNull()
  })
})

describe('countdownBadgeStyle helper (pure)', () => {
  // Pure-function unit tests for the badge selector. The rendered
  // tests above cover integration; these lock down the decision
  // boundaries so the tier cutoffs never drift quietly.

  it('returns null when start is missing', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    expect(countdownBadgeStyle(undefined, undefined, Date.now())).toBeNull()
  })

  it('returns null after the event has ended', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T12:00:00Z').getTime()
    expect(
      countdownBadgeStyle('2026-04-15T09:00:00Z', '2026-04-15T10:00:00Z', now),
    ).toBeNull()
  })

  it('returns green pulse while the event is in progress', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T12:15:00Z').getTime()
    const style = countdownBadgeStyle('2026-04-15T12:00:00Z', '2026-04-15T13:00:00Z', now)
    expect(style).not.toBeNull()
    expect(style!.pulse).toBe(true)
    expect(style!.className).toMatch(/green/)
  })

  it('returns red for under 5 minutes out', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T11:58:00Z').getTime()
    const style = countdownBadgeStyle('2026-04-15T12:00:00Z', '2026-04-15T13:00:00Z', now)
    expect(style!.pulse).toBe(false)
    expect(style!.className).toMatch(/red/)
  })

  it('returns amber in the 5 to 30 minute window', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T11:45:00Z').getTime()
    const style = countdownBadgeStyle('2026-04-15T12:00:00Z', '2026-04-15T13:00:00Z', now)
    expect(style!.className).toMatch(/amber/)
  })

  it('returns blue in the 30 to 60 minute window', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T11:15:00Z').getTime()
    const style = countdownBadgeStyle('2026-04-15T12:00:00Z', '2026-04-15T13:00:00Z', now)
    expect(style!.className).toMatch(/blue/)
  })

  it('returns slate when more than an hour out', async () => {
    const { countdownBadgeStyle } = await import('./Calendar')
    const now = new Date('2026-04-15T09:00:00Z').getTime()
    const style = countdownBadgeStyle('2026-04-15T12:00:00Z', '2026-04-15T13:00:00Z', now)
    expect(style!.className).toMatch(/slate/)
  })
})

describe('Calendar event card layout (badge aligned under time)', () => {
  // The old layout put the time in a right-aligned fixed-width column,
  // which pushed the countdown badge onto a second line inside that
  // same column. Because the badge was wider than the time text, it
  // visually slipped far to the left of the time while the title hung
  // off to the right. The card now stacks as two rows inside a single
  // left-aligned column: row 1 is time + title, row 2 is the badge.
  // Both rows share the same left edge so the eye follows a clean
  // vertical line.

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function renderSoccerGame() {
    const fixedNow = new Date('2026-04-15T18:30:00-05:00')
    vi.setSystemTime(fixedNow)
    const start = new Date(fixedNow.getTime() + 60 * 60000)
    const end = new Date(start.getTime() + 60 * 60000)
    const fakeEvents = [
      {
        id: 'ev-badge',
        summary: 'Fox - soccer game',
        start: { dateTime: start.toISOString() },
        end: { dateTime: end.toISOString() },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: fakeEvents })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Fox - soccer game')).toBeInTheDocument()
    })
  }

  it('test_calendar_badge_left_edge_aligned_with_time', async () => {
    // The badge must share its left edge with the time text. Both sit
    // inside the same left-aligned body column, with no right-aligned
    // fixed-width wrapper pushing the badge away from the time.
    await renderSoccerGame()
    const body = screen.getByTestId('event-body-ev-badge')
    // Body column is left aligned (no text-right anywhere on the wrapper).
    expect(body.className).not.toMatch(/text-right/)
    // Time and badge must both live inside the same body column so they
    // share a starting x-coordinate. The old layout had the time in a
    // separate sibling div from the badge's parent; now they are cousins
    // under one body wrapper.
    const titleEl = screen.getByText('Fox - soccer game')
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    expect(body.contains(titleEl)).toBe(true)
    expect(body.contains(badge)).toBe(true)
    // Badge itself must not be wrapped in a right-aligned column.
    let p: HTMLElement | null = badge.parentElement
    while (p && p !== body) {
      expect(p.className || '').not.toMatch(/text-right/)
      p = p.parentElement
    }
    // The old layout used a min-w-[72px] column to park the time on
    // one side. The new layout must not reintroduce that fixed-width
    // cage, or the badge drifts out of alignment again.
    expect(body.innerHTML).not.toMatch(/min-w-\[72px\]/)
    expect(body.innerHTML).not.toMatch(/min-w-\[64px\]/)
  })

  it('test_calendar_card_layout_two_rows_time_title_then_badge', async () => {
    // The card body must be a vertical stack. Row 1 holds the time and
    // the title side by side. Row 2 holds the countdown badge directly
    // below. The badge must be a sibling of the time+title row, not a
    // child of the time cell. This keeps the badge flush left under
    // the time even when the title is long.
    await renderSoccerGame()
    const body = screen.getByTestId('event-body-ev-badge')
    const badge = screen.getByTestId('countdown-badge-ev-badge')
    const titleEl = screen.getByText('Fox - soccer game')
    // Title sits on its own row (a flex row), and that row is a direct
    // child of the body column.
    const titleRow = titleEl.closest('div')
    expect(titleRow).not.toBeNull()
    expect(titleRow!.parentElement).toBe(body)
    // Badge's direct parent is also the body wrapper, not the title
    // row. That puts it on its own line right under the title row.
    expect(badge.parentElement).toBe(body)
    // The time text and the title must live in the SAME row so row 1
    // reads "time  title" left to right. If the time were elsewhere,
    // the title row would hold only the title.
    const timeP = titleRow!.querySelector('p')
    expect(timeP).not.toBeNull()
    // The row must hold at least two <p> children (time and title).
    expect(titleRow!.querySelectorAll('p').length).toBeGreaterThanOrEqual(2)
  })
})

describe('Calendar refetches on calendar bus bump (chat-driven sync)', () => {
  // Regression guard for the bug where chat creates a Google Calendar
  // event, the event lands in Google, but the Calendar tab shows the
  // pre-event state until the user manually reloads. The fix wires
  // ChatPanel's create_calendar_event tool_result handler to call
  // bumpCalendar(), which the Calendar page listens for via
  // onCalendarChange. On every bump the page refetches events so the
  // just-created event shows up without a reload.

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.calendarCache.v1')
  })

  afterEach(() => {
    window.localStorage.removeItem('myos.calendarCache.v1')
  })

  it('refetches events when bumpCalendar fires', async () => {
    let eventsCalls = 0
    const first = [
      {
        id: 'ev-before',
        summary: 'Before chat created the event',
        start: { dateTime: '2026-04-21T09:00:00-05:00' },
        end: { dateTime: '2026-04-21T09:30:00-05:00' },
      },
    ]
    const second = [
      ...first,
      {
        id: 'ev-after',
        summary: 'Chat just added this event',
        start: { dateTime: '2026-04-21T14:00:00-05:00' },
        end: { dateTime: '2026-04-21T14:30:00-05:00' },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) {
        eventsCalls += 1
        return Promise.resolve({ events: eventsCalls === 1 ? first : second })
      }
      return Promise.resolve({})
    })

    renderCalendar()

    await waitFor(() => {
      expect(screen.getByText('Before chat created the event')).toBeInTheDocument()
    })
    const initialCalls = eventsCalls

    // Simulate ChatPanel's post-tool_result bump.
    const { bumpCalendar } = await import('../lib/sidebarBus')
    bumpCalendar()

    // The bus should trigger a second fetchEvents, after which the new
    // event lands on screen.
    await waitFor(() => {
      expect(screen.getByText('Chat just added this event')).toBeInTheDocument()
    })
    expect(eventsCalls).toBeGreaterThan(initialCalls)
  })
})

describe('Calendar delete event reliability', () => {
  // Regression guard for the "delete does not actually delete" bug Tori
  // reported. Both code paths below exercise real user journeys:
  //   - "Google-synced" event: the event is returned by the normal
  //     /calendar/events pull. Its id is a plain Google event id.
  //   - "Chat-created" event: the event is created via the chat tool
  //     create_calendar_event. Because the tool writes directly through
  //     services.calendar.create_event, the event comes back with the
  //     SAME id shape as a Google-synced event, but the Calendar tab
  //     only sees it after a bumpCalendar() refetch. The delete must
  //     work just as reliably against that id.
  // In both cases the regression is:
  //   1. The delete button optimistically hides the row.
  //   2. A concurrent refetch (chat creates another event, Sync fires,
  //      focus rehydrate) replaces the list with fresh server data,
  //      and the server still has the just-deleted event because the
  //      5-second undo window has not expired. The deleted event
  //      reappears on screen.
  // The fix tracks pending-deleted ids and filters them out of every
  // setEvents that runs while the delete is in flight.

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.calendarCache.v1')
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    window.localStorage.removeItem('myos.calendarCache.v1')
    vi.useRealTimers()
  })

  async function setupWithEvents(events: Array<{ id: string; summary: string }>) {
    const fullEvents = events.map((e) => ({
      ...e,
      start: { dateTime: '2026-04-22T09:00:00-05:00' },
      end: { dateTime: '2026-04-22T10:00:00-05:00' },
    }))
    let eventsCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) {
        eventsCalls += 1
        return Promise.resolve({ events: fullEvents })
      }
      return Promise.resolve({})
    })
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({ ok: true } as never)
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText(events[0].summary)).toBeInTheDocument()
    })
    return { mockedApiDelete, fullEventsRef: fullEvents, eventsCallsRef: () => eventsCalls }
  }

  it('deletes a Google-synced event: DELETE hits the right id and the row stays gone', async () => {
    const { mockedApiDelete } = await setupWithEvents([
      { id: 'google-synced-123', summary: 'Dentist appointment' },
    ])

    const deleteBtn = screen.getByTestId('delete-event-google-synced-123')
    fireEvent.click(deleteBtn)

    // Row hides immediately (optimistic).
    expect(screen.queryByText('Dentist appointment')).not.toBeInTheDocument()

    // Let the 5 second undo timer run.
    await vi.advanceTimersByTimeAsync(5100)

    expect(mockedApiDelete).toHaveBeenCalledWith(
      '/calendar/events/google-synced-123'
    )
    // Row must not come back.
    expect(screen.queryByText('Dentist appointment')).not.toBeInTheDocument()
  })

  it('deletes a chat-created event (same id shape) the same way', async () => {
    // Chat-created events land on the tab after ChatPanel fires
    // bumpCalendar(). Their id is whatever Google returned from the
    // insert call, same shape as any other synced event.
    const { mockedApiDelete } = await setupWithEvents([
      { id: 'chat-made-evt-xyz_20260422T140000Z', summary: 'Pepper field trip' },
    ])

    const deleteBtn = screen.getByTestId(
      'delete-event-chat-made-evt-xyz_20260422T140000Z'
    )
    fireEvent.click(deleteBtn)
    expect(screen.queryByText('Pepper field trip')).not.toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(5100)

    expect(mockedApiDelete).toHaveBeenCalledWith(
      '/calendar/events/chat-made-evt-xyz_20260422T140000Z'
    )
    expect(screen.queryByText('Pepper field trip')).not.toBeInTheDocument()
  })

  it('keeps a just-deleted event hidden when a refetch lands during the undo window', async () => {
    // This is the core bug: during the 5s undo window, something else
    // (chat creates a new event, Sync button, focus) triggers a
    // refetch. The backend still returns the to-be-deleted event.
    // Without the pending-id filter, that event reappears on the page.
    const full = [
      {
        id: 'to-delete-1',
        summary: 'Soon-to-be-deleted meeting',
        start: { dateTime: '2026-04-22T09:00:00-05:00' },
        end: { dateTime: '2026-04-22T10:00:00-05:00' },
      },
      {
        id: 'keep-1',
        summary: 'Other meeting',
        start: { dateTime: '2026-04-22T11:00:00-05:00' },
        end: { dateTime: '2026-04-22T12:00:00-05:00' },
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: full })
      return Promise.resolve({})
    })
    vi.mocked(api.delete).mockResolvedValue({ ok: true } as never)

    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Soon-to-be-deleted meeting')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('delete-event-to-delete-1'))
    expect(screen.queryByText('Soon-to-be-deleted meeting')).not.toBeInTheDocument()

    // Mid-undo: something else (e.g. chat created an event) bumps the
    // calendar bus, which triggers a fresh fetch. The backend has not
    // yet received our DELETE call, so it still returns the event.
    const { bumpCalendar } = await import('../lib/sidebarBus')
    bumpCalendar()

    // The deleted event must NOT reappear even after the refetch.
    await waitFor(() => {
      expect(screen.getByText('Other meeting')).toBeInTheDocument()
    })
    expect(screen.queryByText('Soon-to-be-deleted meeting')).not.toBeInTheDocument()

    // And once the 5s timer fires, the delete is still sent.
    await vi.advanceTimersByTimeAsync(5100)
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith(
      '/calendar/events/to-delete-1'
    )
  })

  it('purges the deleted event from the localStorage seed cache after a successful delete', async () => {
    // Without this, the next page load repaints the deleted event for
    // ~400ms while the background fetch runs. That reads as the delete
    // "didn't actually work" to the user.
    const stored = [
      {
        id: 'zap-me',
        summary: 'Delete me please',
        start: { dateTime: '2026-04-22T09:00:00-05:00' },
        end: { dateTime: '2026-04-22T10:00:00-05:00' },
      },
      {
        id: 'keep-me',
        summary: 'Keep me',
        start: { dateTime: '2026-04-22T11:00:00-05:00' },
        end: { dateTime: '2026-04-22T12:00:00-05:00' },
      },
    ]
    window.localStorage.setItem('myos.calendarCache.v1', JSON.stringify(stored))

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/calendar/events')) return Promise.resolve({ events: stored })
      return Promise.resolve({})
    })
    vi.mocked(api.delete).mockResolvedValue({ ok: true } as never)

    renderCalendar()
    await waitFor(() => {
      expect(screen.getByText('Delete me please')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('delete-event-zap-me'))
    await vi.advanceTimersByTimeAsync(5100)
    // Flush any queued microtasks from the async delete handler.
    await vi.runOnlyPendingTimersAsync()

    const cached = JSON.parse(
      window.localStorage.getItem('myos.calendarCache.v1') || '[]'
    )
    const cachedIds = (cached as Array<{ id: string }>).map((e) => e.id)
    expect(cachedIds).not.toContain('zap-me')
    expect(cachedIds).toContain('keep-me')
  })
})

describe('Calendar — Google OAuth connect button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.calendarCache.v1')
  })

  it('shows the OAuth button when google_oauth_available is true and not authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status'))
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null })
      if (path.includes('/secrets/key-status'))
        return Promise.resolve({ google_oauth_available: true })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-button-calendar')).toBeInTheDocument()
    })
  })

  it('does not show the OAuth button when google_oauth_available is false', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/calendar/auth/status'))
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null })
      if (path.includes('/secrets/key-status'))
        return Promise.resolve({ google_oauth_available: false })
      return Promise.resolve({})
    })
    renderCalendar()
    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-google-button-calendar')).not.toBeInTheDocument()
  })
})
