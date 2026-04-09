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


describe('Gmail page inbox rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the loading spinner first and never flashes the empty state before the fetch resolves', async () => {
    // Make the messages call hang until we resolve it manually so we can
    // assert the loading state is visible during the pending window.
    let resolveMessages: (value: { messages: unknown[] }) => void = () => {}
    const messagesPromise = new Promise<{ messages: unknown[] }>((resolve) => {
      resolveMessages = resolve
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return messagesPromise
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })

    renderGmail()

    // While the fetch is pending the page shows the top-level Loading...
    // spinner and MUST NOT render the "No messages in your inbox." copy.
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByText(/No messages in your inbox\./i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Your inbox is clear\./i)).not.toBeInTheDocument()

    // Now resolve with zero messages.
    resolveMessages({ messages: [] })

    // Only AFTER the fetch resolves with an empty list does the empty
    // state render.
    await waitFor(() => {
      expect(screen.getByText(/No messages in your inbox\./i)).toBeInTheDocument()
    })
  })

  it('renders all inbox messages (read AND unread), not just unread', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) {
        return Promise.resolve({
          messages: [
            {
              id: 'u1',
              thread_id: 't1',
              subject: 'Unread subject 1',
              from_name: 'Unread Sender 1',
              from_email: 'u1@example.com',
              snippet: 'snippet u1',
              date: '2026-04-08T10:00:00+00:00',
              is_unread: true,
            },
            {
              id: 'u2',
              thread_id: 't2',
              subject: 'Unread subject 2',
              from_name: 'Unread Sender 2',
              from_email: 'u2@example.com',
              snippet: 'snippet u2',
              date: '2026-04-08T09:30:00+00:00',
              is_unread: true,
            },
            {
              id: 'r1',
              thread_id: 't3',
              subject: 'Read subject 1',
              from_name: 'Read Sender 1',
              from_email: 'r1@example.com',
              snippet: 'snippet r1',
              date: '2026-04-07T15:00:00+00:00',
              is_unread: false,
            },
          ],
        })
      }
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })

    renderGmail()

    // Wait for both unread AND read subjects to be present. If the old
    // unread-only filter sneaks back in, Read subject 1 will be missing
    // and this test will fail.
    await waitFor(() => {
      expect(screen.getByText('Unread subject 1')).toBeInTheDocument()
    })
    expect(screen.getByText('Unread subject 2')).toBeInTheDocument()
    expect(screen.getByText('Read subject 1')).toBeInTheDocument()

    // The badge should reflect the unread count (2), not the total (3).
    const header = screen.getByRole('heading', { name: 'Gmail' }).parentElement
    expect(header).not.toBeNull()
    // The badge is a sibling span with the unread count.
    expect(header!.textContent).toContain('2')
  })
})


describe('Gmail page Reconnect Gmail CTA', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const MESSAGE = {
    id: 'm1',
    thread_id: 't1',
    subject: 'A subject',
    from_name: 'Alice',
    from_email: 'alice@example.com',
    snippet: 'Hello there, this is a snippet.',
    date: '2026-04-08T10:00:00+00:00',
    is_unread: false,
  }

  const GOOGLE_OAUTH_URL =
    'https://accounts.google.com/o/oauth2/v2/auth' +
    '?client_id=209690833139-test.apps.googleusercontent.com' +
    '&scope=openid+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send' +
    '&response_type=code&state=abc'

  function mockApiForReconnect(reauthUrl: string | null) {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.resolve({ messages: [MESSAGE] })
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: false, reauth_url: reauthUrl })
      }
      return Promise.resolve({})
    })
  }

  it('renders the Reconnect Gmail CTA with the real Google OAuth URL as href, not the API path', async () => {
    mockApiForReconnect(GOOGLE_OAUTH_URL)

    renderGmail()

    // Wait for the message to render, then expand it so the reply row shows.
    const messageButton = await screen.findByText('A subject')
    fireEvent.click(messageButton)

    // The reply row shows the Reconnect CTA instead of the Reply button
    // because has_send_scope is false.
    const link = await screen.findByRole('link', { name: /Connect Gmail to send replies/i })

    const href = link.getAttribute('href') || ''

    // The critical assertion: the href must be the real Google consent
    // URL, never the API endpoint path that returns JSON.
    expect(href).toBe(GOOGLE_OAUTH_URL)
    expect(href.startsWith('https://accounts.google.com/')).toBe(true)
    expect(href).not.toBe('/api/drive/auth/url/gmail')
    expect(href.startsWith('/api/')).toBe(false)
  })

  it('does not render the Reconnect CTA when the backend returns a null reauth_url', async () => {
    mockApiForReconnect(null)

    renderGmail()

    const messageButton = await screen.findByText('A subject')
    fireEvent.click(messageButton)

    // Fallback copy should show instead of a broken link.
    await waitFor(() => {
      expect(
        screen.getByText(/Connect Gmail with send permission to reply from here\./i)
      ).toBeInTheDocument()
    })

    expect(
      screen.queryByRole('link', { name: /Connect Gmail to send replies/i })
    ).not.toBeInTheDocument()
  })

  it('does not render raw JSON for the reconnect CTA', async () => {
    // Defensive: this is the exact symptom Tori saw. Even if the
    // backend handed back a bogus reauth_url, the page must not render
    // JSON-looking text inline.
    mockApiForReconnect(GOOGLE_OAUTH_URL)

    renderGmail()

    const messageButton = await screen.findByText('A subject')
    fireEvent.click(messageButton)

    await screen.findByRole('link', { name: /Connect Gmail to send replies/i })

    // The rendered DOM must not contain a stringified JSON envelope
    // with a "url" key, which is what Tori's broken browser showed.
    expect(document.body.textContent).not.toMatch(/\{\s*"url"\s*:/)
  })
})
