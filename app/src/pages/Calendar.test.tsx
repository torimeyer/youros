import { describe, it, expect, vi, beforeEach } from 'vitest'
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
    },
  }
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

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
