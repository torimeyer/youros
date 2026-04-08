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
