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

describe('Gmail connect error', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows inline error instead of browser alert when connect fails', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) {
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      }
      if (path.includes('/drive/auth/url/gmail')) {
        return Promise.reject(new Error('Failed to fetch'))
      }
      return Promise.resolve({})
    })

    renderGmail()

    const connectButton = await screen.findByRole('button', { name: /Connect Google account/i })
    fireEvent.click(connectButton)

    await waitFor(() => {
      expect(screen.getByText(/Could not get the sign-in link/i)).toBeInTheDocument()
    })
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

    // While the fetch is pending the page shows the top-level loading
    // spinner and MUST NOT render the empty state.
    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
    expect(screen.queryByTestId('empty-state')).not.toBeInTheDocument()

    // Now resolve with zero messages.
    resolveMessages({ messages: [] })

    // Only AFTER the fetch resolves with an empty list does the empty
    // state render.
    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
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
    const headings = screen.getAllByRole('heading', { name: 'Gmail' })
    const header = (headings.find(h => h.getAttribute('data-testid') !== 'topbar-title') ?? headings[0]).parentElement
    expect(header).not.toBeNull()
    // The badge is a sibling span with the unread count.
    expect(header!.textContent).toContain('2')
  })

  it('defaults to unread-first: an unread message sorts above a newer read one (→1387/→1672)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) {
        return Promise.resolve({
          messages: [
            {
              id: 'rNew', thread_id: 't1', subject: 'Read newest',
              from_name: 'R', from_email: 'r@example.com', snippet: 's',
              date: '2026-04-10T10:00:00+00:00', is_unread: false,
            },
            {
              id: 'uOld', thread_id: 't2', subject: 'Unread older',
              from_name: 'U', from_email: 'u@example.com', snippet: 's',
              date: '2026-04-08T10:00:00+00:00', is_unread: true,
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

    await waitFor(() => {
      expect(screen.getByText('Unread older')).toBeInTheDocument()
    })
    expect(screen.getByText('Read newest')).toBeInTheDocument()

    // By default (unread-at-top ON), the unread message must render BEFORE the
    // newer read one — proving the toggle is on by default AND the sort is
    // applied to the rendered list, not just computed. This is the →1672
    // report ("toggle does nothing / should default on"): the feature shipped
    // under →1387 and this guards it.
    const unread = screen.getByText('Unread older')
    const read = screen.getByText('Read newest')
    expect(
      unread.compareDocumentPosition(read) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
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

describe('Gmail ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.gmailCache.v1')
  })

  it('renders ConnectCard with red accent when not authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) {
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      }
      return Promise.resolve({})
    })

    renderGmail()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // Red accent color: #ef4444 -> jsdom converts to rgb(239, 68, 68)
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/239, 68, 68/)
  })

  it('renders ConnectCard with reauth title when needs_reauth is true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) {
        return Promise.resolve({ authenticated: true, needs_reauth: true, email: null, unread_count: 0 })
      }
      return Promise.resolve({})
    })

    renderGmail()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.getByText(/Gmail access needs to be updated/i)).toBeInTheDocument()
  })

  it('shows ConnectCard when /gmail/messages returns 403 needs_reauth (→1575)', async () => {
    // Regression guard: when the refresh token is revoked, auth/status
    // still says needs_reauth=false (it only checks file existence), but
    // the messages endpoint returns 403 with {needs_reauth: true}. The
    // frontend must flip the ConnectCard on immediately instead of showing
    // an empty inbox with no explanation.
    const reauthError = new ApiError(
      403,
      JSON.stringify({ detail: { needs_reauth: true, message: 'Your Google account connection has expired.' } }),
    )
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/send_capability')) return Promise.resolve({ has_send_scope: false, reauth_url: null })
      if (path.includes('/gmail/messages')) return Promise.reject(reauthError)
      return Promise.resolve({})
    })

    renderGmail()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.getByText(/Gmail access needs to be updated/i)).toBeInTheDocument()
  })

  it('shows a setup guide link in the connect panel and opens the guide modal when clicked', async () => {
    // Regression guard: a person who opens the Gmail tab before finishing
    // their Google Cloud setup needs a way to find the instructions. The
    // primary button launches OAuth; the secondary link opens the shared
    // setup guide modal.
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) {
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      }
      return Promise.resolve({})
    })

    renderGmail()

    const guideLink = await screen.findByText(/Need setup help\? See the guide/i)
    expect(guideLink).toBeInTheDocument()

    // Modal is not rendered until the link is clicked.
    expect(screen.queryByTestId('google-setup-guide-modal')).not.toBeInTheDocument()

    fireEvent.click(guideLink)

    await waitFor(() => {
      expect(screen.getByTestId('google-setup-guide-modal')).toBeInTheDocument()
    })
  })
})


describe('Gmail preview overflow containment', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.gmailCache.v1')
  })

  const LONG_SNIPPET =
    'This is an extremely long snippet with a huge uninterruptedtokenthatwouldotherwiseblowoutofthecardboundarywithoutwordbreakrules and also https://example.com/very/long/url/that/should/not/push/the/layout/sideways/past/the/card/edge/when/rendered/in/the/collapsed/row/or/in/the/expanded/body/view'

  const LONG_SUBJECT =
    'Returned Scheduleabsolutelymassivesubjectlinewithanunbreakablestringthatcouldotherwisepushthecardsideways'

  const MESSAGE = {
    id: 'overflow1',
    thread_id: 't-overflow',
    subject: LONG_SUBJECT,
    from_name: 'Sender With A Rather Long Display Name',
    from_email: 'extremely.long.email.address.that.could.push.the.layout@example-domain-that-is-unreasonable.com',
    snippet: LONG_SNIPPET,
    date: '2026-04-15T10:00:00+00:00',
    is_unread: true,
  }

  function mockAuthenticated() {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.resolve({ messages: [MESSAGE] })
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })
  }

  it('email row snippet truncates and does not overflow card', async () => {
    mockAuthenticated()
    renderGmail()

    // Wait for the row to render.
    const snippetEl = await screen.findByText(LONG_SNIPPET)
    expect(snippetEl).toBeInTheDocument()
    // The snippet <p> must carry the truncate class (overflow:hidden +
    // text-overflow:ellipsis + white-space:nowrap). Without this, long
    // unbroken snippets overflow the card horizontally. Needle 357.
    expect(snippetEl.className).toContain('truncate')

    // The subject <p> must also truncate.
    const subjectEl = screen.getByText(LONG_SUBJECT)
    expect(subjectEl.className).toContain('truncate')
  })

  it('row button has min-w-0 so truncate children can actually shrink', async () => {
    mockAuthenticated()
    renderGmail()

    // The row button wraps the preview block. Its class must contain
    // min-w-0, otherwise the flex-1 button holds its content min-width
    // and the truncate inside never kicks in. This is the exact bug
    // where the preview bled off the right card edge.
    const rowButton = await screen.findByRole('button', {
      name: /Returned Schedule/i,
    })
    expect(rowButton.className).toContain('min-w-0')
    expect(rowButton.className).toContain('flex-1')
  })

  it('expanded snippet body wraps long unbreakable tokens with break-words', async () => {
    mockAuthenticated()
    renderGmail()

    // Expand the row.
    const rowButton = await screen.findByRole('button', {
      name: /Returned Schedule/i,
    })
    fireEvent.click(rowButton)

    // The expanded snippet <p> must have break-words so long URLs and
    // unbreakable tokens wrap inside the card instead of pushing it
    // sideways. whitespace-pre-wrap alone does NOT break long tokens.
    const paragraphs = screen.getAllByText(LONG_SNIPPET)
    // The expanded body paragraph is the one with whitespace-pre-wrap.
    const expanded = paragraphs.find((el) =>
      el.className.includes('whitespace-pre-wrap')
    )
    expect(expanded).toBeDefined()
    expect(expanded!.className).toContain('break-words')
  })
})

describe('Gmail delete (Trash) actions', () => {
  const mockedApiDelete = vi.mocked(api.delete)
  const mockedApiPost = vi.mocked(api.post)

  const MESSAGES = [
    {
      id: 'm1',
      thread_id: 't1',
      subject: 'Subject one',
      from_name: 'Amazon',
      from_email: 'a@example.com',
      snippet: 'Sale snippet',
      date: '2026-04-08T10:00:00+00:00',
      is_unread: false,
    },
    {
      id: 'm2',
      thread_id: 't2',
      subject: 'Subject two',
      from_name: 'Amazon',
      from_email: 'a@example.com',
      snippet: 'Deal snippet',
      date: '2026-04-08T11:00:00+00:00',
      is_unread: false,
    },
  ]

  function mockAuthenticated() {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.resolve({ messages: MESSAGES })
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.gmailCache.v1')
  })

  it('Move to Trash button calls DELETE and removes the row', async () => {
    mockAuthenticated()
    mockedApiDelete.mockResolvedValue({ ok: true, permanent: false, id: 'm1' })

    renderGmail()

    const subject = await screen.findByText('Subject one')
    fireEvent.click(subject)

    const trashBtn = await screen.findByRole('button', { name: /Move to Trash/i })
    fireEvent.click(trashBtn)

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/gmail/messages/m1')
    })
    await waitFor(() => {
      expect(screen.queryByText('Subject one')).not.toBeInTheDocument()
    })
    // Other row still visible.
    expect(screen.getByText('Subject two')).toBeInTheDocument()
  })

  it('bulk Trash selected sends batch-delete with selected ids and confirms', async () => {
    mockAuthenticated()
    mockedApiPost.mockImplementation((path: string, body?: unknown) => {
      if (path.includes('/gmail/messages/batch-delete')) {
        const ids = (body as { ids: string[] }).ids
        return Promise.resolve({ succeeded: ids, failed: [], count: ids.length })
      }
      return Promise.resolve({})
    })
    const confirmSpy = vi.spyOn(window, 'confirm')

    renderGmail()

    // Select both messages via their checkboxes.
    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Amazon/i,
    })
    expect(checkboxes.length).toBe(2)
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    // Bulk action bar should now show 2 selected and a Trash button.
    const bulkBtn = await screen.findByRole('button', {
      name: /Trash selected messages/i,
    })
    fireEvent.click(bulkBtn)

    // In-app modal appears; app must not call window.confirm.
    const confirmModalBtn = await screen.findByTestId('confirm-modal-confirm')
    expect(confirmSpy).not.toHaveBeenCalled()
    fireEvent.click(confirmModalBtn)

    await waitFor(() => {
      const call = mockedApiPost.mock.calls.find((c) =>
        String(c[0]).includes('/gmail/messages/batch-delete')
      )
      expect(call).toBeTruthy()
      expect((call![1] as { ids: string[]; permanent: boolean }).ids.sort()).toEqual([
        'm1',
        'm2',
      ])
      expect((call![1] as { ids: string[]; permanent: boolean }).permanent).toBe(false)
    })
    await waitFor(() => {
      expect(screen.queryByText('Subject one')).not.toBeInTheDocument()
      expect(screen.queryByText('Subject two')).not.toBeInTheDocument()
    })

    confirmSpy.mockRestore()
  })

  it('bulk Trash aborts when the user cancels the in-app confirm dialog', async () => {
    mockAuthenticated()
    const confirmSpy = vi.spyOn(window, 'confirm')

    renderGmail()

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Amazon/i,
    })
    fireEvent.click(checkboxes[0])

    const bulkBtn = await screen.findByRole('button', {
      name: /Trash selected messages/i,
    })
    fireEvent.click(bulkBtn)

    // The in-app modal should appear. Click Cancel.
    const cancelBtn = await screen.findByTestId('confirm-modal-cancel')
    fireEvent.click(cancelBtn)

    // batch-delete must NOT fire when the user cancels.
    expect(
      mockedApiPost.mock.calls.find((c) =>
        String(c[0]).includes('/gmail/messages/batch-delete')
      )
    ).toBeUndefined()
    // Row still visible.
    expect(screen.getByText('Subject one')).toBeInTheDocument()
    // The browser-native confirm must never be called.
    expect(confirmSpy).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })
})

describe('Gmail — localStorage cache reflects read state (→1688)', () => {
  const mockedApiPost = vi.mocked(api.post)

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.gmailCache.v1')
  })

  it('updates localStorage is_unread to false when user opens an unread message', async () => {
    const MESSAGE = {
      id: 'unread1',
      thread_id: 't1',
      subject: 'Your health records are ready to view',
      from_name: 'Health Records Online',
      from_email: 'noreply@healthrecords.com',
      snippet: 'Log in to view your records.',
      date: '2026-05-24T10:00:00+00:00',
      is_unread: true,
    }

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.resolve({ messages: [MESSAGE] })
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({})

    renderGmail()

    // Wait for the message to render and the localStorage cache to be written.
    await waitFor(() => {
      const cached = JSON.parse(window.localStorage.getItem('myos.gmailCache.v1') || '[]') as Array<{ id: string; is_unread: boolean }>
      expect(cached.some((m) => m.id === 'unread1' && m.is_unread === true)).toBe(true)
    })

    // Click the message row to expand it — this should mark it as read.
    // Use the subject text which is unique (from_name is different).
    const messageButton = await screen.findByText('Your health records are ready to view')
    fireEvent.click(messageButton)

    // Wait for the mark-read API to be called.
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/gmail/messages/unread1/read', {})
    })

    // The localStorage cache must now have is_unread: false for this message.
    // Without the fix the cache still holds is_unread: true, so the next page
    // load shows the already-read email with a blue dot until the slow
    // background fetch completes. Regression guard for →1688.
    const cached = JSON.parse(window.localStorage.getItem('myos.gmailCache.v1') || '[]') as Array<{ id: string; is_unread: boolean }>
    const entry = cached.find((m) => m.id === 'unread1')
    expect(entry).toBeDefined()
    expect(entry!.is_unread).toBe(false)
  })

  it('does not show blue dot for a previously-read email when initialized from stale localStorage', async () => {
    // Seed localStorage with a message marked as read (simulating the state
    // after the fix has persisted the read state). The page must not render
    // a blue dot for it. This tests the initialization path that caused →1688.
    const staleCache = [
      {
        id: 'read1',
        thread_id: 't1',
        subject: 'Your DoorDash order has arrived',
        from_name: 'DoorDash',
        from_email: 'noreply@doordash.com',
        snippet: 'Your order is here.',
        date: '2026-05-24T08:00:00+00:00',
        is_unread: false,
      },
    ]
    window.localStorage.setItem('myos.gmailCache.v1', JSON.stringify(staleCache))

    // Make the API hang so we can assert the initial cache-seeded render.
    let resolveMessages!: (v: { messages: unknown[] }) => void
    const messagesPromise = new Promise<{ messages: unknown[] }>((res) => { resolveMessages = res })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return messagesPromise
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })

    renderGmail()

    // While the network fetch is pending the page uses the localStorage seed.
    // The message should render without a blue dot.
    await waitFor(() => {
      expect(screen.getByText('Your DoorDash order has arrived')).toBeInTheDocument()
    })

    // Find the row button that contains this subject and check no blue dot.
    const subjectEl = screen.getByText('Your DoorDash order has arrived')
    const rowContainer = subjectEl.closest('button')
    expect(rowContainer).not.toBeNull()
    // A blue dot would be a span with bg-blue-400 inside this row.
    const blueDots = rowContainer!.querySelectorAll('span.bg-blue-400')
    expect(blueDots.length).toBe(0)

    // Clean up the pending promise.
    resolveMessages({ messages: staleCache })
  })
})

describe('Gmail bulk mark as read (→2473)', () => {
  const mockedApiPost = vi.mocked(api.post)

  const MESSAGES = [
    {
      id: 'm1',
      thread_id: 't1',
      subject: 'Unread email from Alice',
      from_name: 'Alice',
      from_email: 'alice@example.com',
      snippet: 'First unread snippet',
      date: '2026-07-06T10:00:00+00:00',
      is_unread: true,
    },
    {
      id: 'm2',
      thread_id: 't2',
      subject: 'Another unread from Alice',
      from_name: 'Alice',
      from_email: 'alice@example.com',
      snippet: 'Second unread snippet',
      date: '2026-07-06T09:00:00+00:00',
      is_unread: true,
    },
  ]

  function mockAuthenticated() {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/gmail/messages')) return Promise.resolve({ messages: MESSAGES })
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: true, reauth_url: null })
      }
      return Promise.resolve({})
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.gmailCache.v1')
  })

  it('"Mark as read" button appears in the bulk bar when messages are selected', async () => {
    mockAuthenticated()
    renderGmail()

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Alice/i,
    })
    fireEvent.click(checkboxes[0])

    const markReadBtn = await screen.findByRole('button', { name: /Mark as read/i })
    expect(markReadBtn).toBeInTheDocument()
  })

  it('"Mark as read" button is not visible when no messages are selected', async () => {
    mockAuthenticated()
    renderGmail()

    await screen.findByText('Unread email from Alice')
    expect(screen.queryByRole('button', { name: /Mark as read/i })).not.toBeInTheDocument()
  })

  it('clicking "Mark as read" calls batch-mark-read with all selected ids', async () => {
    mockAuthenticated()
    mockedApiPost.mockImplementation((path: string, body?: unknown) => {
      if (path.includes('/gmail/messages/batch-mark-read')) {
        const ids = (body as { ids: string[] }).ids
        return Promise.resolve({ succeeded: ids, failed: [], count: ids.length })
      }
      return Promise.resolve({})
    })

    renderGmail()

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Alice/i,
    })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    const markReadBtn = await screen.findByRole('button', { name: /Mark as read/i })
    fireEvent.click(markReadBtn)

    await waitFor(() => {
      const call = mockedApiPost.mock.calls.find((c) =>
        String(c[0]).includes('/gmail/messages/batch-mark-read')
      )
      expect(call).toBeTruthy()
      expect((call![1] as { ids: string[] }).ids.sort()).toEqual(['m1', 'm2'])
    })
  })

  it('marked messages lose their unread blue dot after success', async () => {
    mockAuthenticated()
    mockedApiPost.mockImplementation((path: string, body?: unknown) => {
      if (path.includes('/gmail/messages/batch-mark-read')) {
        const ids = (body as { ids: string[] }).ids
        return Promise.resolve({ succeeded: ids, failed: [], count: ids.length })
      }
      return Promise.resolve({})
    })

    renderGmail()

    // Both messages start unread — two blue dots.
    await waitFor(() => {
      expect(document.querySelectorAll('span.bg-blue-400').length).toBe(2)
    })

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Alice/i,
    })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    const markReadBtn = await screen.findByRole('button', { name: /Mark as read/i })
    fireEvent.click(markReadBtn)

    await waitFor(() => {
      expect(document.querySelectorAll('span.bg-blue-400').length).toBe(0)
    })
  })

  it('selection clears after all messages are marked as read', async () => {
    mockAuthenticated()
    mockedApiPost.mockImplementation((path: string, body?: unknown) => {
      if (path.includes('/gmail/messages/batch-mark-read')) {
        const ids = (body as { ids: string[] }).ids
        return Promise.resolve({ succeeded: ids, failed: [], count: ids.length })
      }
      return Promise.resolve({})
    })

    renderGmail()

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Alice/i,
    })
    fireEvent.click(checkboxes[0])

    await screen.findByText(/1 selected/i)

    const markReadBtn = await screen.findByRole('button', { name: /Mark as read/i })
    fireEvent.click(markReadBtn)

    await waitFor(() => {
      expect(screen.queryByText(/selected/i)).not.toBeInTheDocument()
    })
  })

  it('shows a partial failure error; succeeded ids still lose unread styling', async () => {
    mockAuthenticated()
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/messages/batch-mark-read')) {
        return Promise.resolve({
          succeeded: ['m1'],
          failed: [{ id: 'm2', error: 'Server error' }],
          count: 1,
        })
      }
      return Promise.resolve({})
    })

    renderGmail()

    const checkboxes = await screen.findAllByRole('checkbox', {
      name: /Select message from Alice/i,
    })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    const markReadBtn = await screen.findByRole('button', { name: /Mark as read/i })
    fireEvent.click(markReadBtn)

    // Partial failure message should appear.
    await waitFor(() => {
      expect(screen.getByText(/1 failed/i)).toBeInTheDocument()
    })

    // The one that succeeded loses its blue dot; the failed one keeps it.
    await waitFor(() => {
      expect(document.querySelectorAll('span.bg-blue-400').length).toBe(1)
    })
  })
})

describe('Gmail — Google OAuth connect button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the OAuth button when google_oauth_available is true and not authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status'))
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      if (path.includes('/secrets/key-status'))
        return Promise.resolve({ google_oauth_available: true })
      return Promise.resolve({})
    })
    renderGmail()
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-button-gmail')).toBeInTheDocument()
    })
  })

  it('does not show the OAuth button when google_oauth_available is false', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/auth/status'))
        return Promise.resolve({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      if (path.includes('/secrets/key-status'))
        return Promise.resolve({ google_oauth_available: false })
      return Promise.resolve({})
    })
    renderGmail()
    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-google-button-gmail')).not.toBeInTheDocument()
  })
})
