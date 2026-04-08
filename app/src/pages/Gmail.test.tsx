import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Gmail from './Gmail'
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
  unread_count: 0,
}

function renderGmail() {
  return render(
    <MemoryRouter>
      <Gmail />
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
      message: 'Gmail API is not enabled in this Google Cloud project.',
    },
  })
  return new ApiError(403, body)
}

describe('Gmail page api_not_enabled screen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the Enable Gmail API link when api_not_enabled is true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) {
        return Promise.resolve(AUTHENTICATED)
      }
      if (path.includes('/gmail/messages')) {
        return Promise.reject(makeApiNotEnabledError())
      }
      return Promise.resolve({})
    })

    renderGmail()

    await waitFor(() => {
      expect(screen.getByText('Gmail API not enabled')).toBeInTheDocument()
    })

    const link = screen.getByRole('link', { name: /Enable Gmail API in Google Cloud/i })
    expect(link).toBeInTheDocument()
  })

  it('points the link to the correct Google Cloud Console URL', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.reject(makeApiNotEnabledError())
      return Promise.resolve({})
    })

    renderGmail()

    const link = await screen.findByRole('link', { name: /Enable Gmail API in Google Cloud/i })
    expect(link.getAttribute('href')).toBe(
      'https://console.cloud.google.com/apis/library/gmail.googleapis.com'
    )
  })

  it('opens the link in a new tab with rel noreferrer', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.reject(makeApiNotEnabledError())
      return Promise.resolve({})
    })

    renderGmail()

    const link = await screen.findByRole('link', { name: /Enable Gmail API in Google Cloud/i })
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  })

  it('Retry button re-fetches messages and clears the not-enabled state on success', async () => {
    let messageCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) {
        messageCalls += 1
        if (messageCalls === 1) {
          return Promise.reject(makeApiNotEnabledError())
        }
        return Promise.resolve({ messages: [] })
      }
      return Promise.resolve({})
    })

    renderGmail()

    const retryButton = await screen.findByRole('button', { name: /Retry/i })
    expect(retryButton).toBeInTheDocument()

    fireEvent.click(retryButton)

    await waitFor(() => {
      expect(screen.queryByText('Gmail API not enabled')).not.toBeInTheDocument()
    })
    expect(messageCalls).toBeGreaterThanOrEqual(2)
  })
})
