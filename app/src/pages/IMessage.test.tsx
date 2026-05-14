import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import IMessage from './IMessage'

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

// jsdom does not provide window.matchMedia
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

const AVAILABLE_STATUS = { available: true, reason: null }
const UNAVAILABLE_STATUS = { available: false, reason: 'iMessage database not found. This feature only works on macOS.' }

function makeConversations(n: number = 3) {
  return Array.from({ length: n }, (_, i) => ({
    id: i,
    identifier: `+1555000${String(i).padStart(4, '0')}`,
    display_name: `Contact ${i}`,
    service: 'iMessage',
    last_message_date: `2026-04-10T10:${String(i).padStart(2, '0')}:00+00:00`,
    last_message_preview: `Hey, this is message ${i}`,
    message_count: 10 + i,
    unread_count: i % 2,
  }))
}

function renderIMessage() {
  return render(
    <MemoryRouter>
      <IMessage />
    </MemoryRouter>
  )
}

describe('iMessage page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear localStorage cache
    window.localStorage.removeItem('myos.imessageCache.v1')
    window.localStorage.removeItem('myos.imessageConnection.v1')
  })

  it('shows loading spinner initially', async () => {
    // Make the status call hang so we see the loading state
    let resolveStatus: (v: unknown) => void = () => {}
    const statusPromise = new Promise((resolve) => {
      resolveStatus = resolve
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return statusPromise as Promise<unknown>
      return Promise.resolve({})
    })

    renderIMessage()

    expect(screen.getByTestId('loading-state')).toBeInTheDocument()

    resolveStatus(AVAILABLE_STATUS)
  })

  it('shows unavailable screen when iMessage is not available', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(UNAVAILABLE_STATUS)
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('iMessage not available')).toBeInTheDocument()
    })
    expect(screen.getByText(/macOS/)).toBeInTheDocument()
  })

  it('shows Full Disk Access instructions when that is the issue', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) {
        return Promise.resolve({
          available: false,
          reason: 'Cannot read the iMessage database. Go to System Settings > Privacy & Security > Full Disk Access and enable access.',
        })
      }
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('iMessage not available')).toBeInTheDocument()
    })
    expect(screen.getByText('How to enable:')).toBeInTheDocument()
  })

  it('renders conversations when available', async () => {
    const convos = makeConversations(3)

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: convos })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('Contact 0')).toBeInTheDocument()
    })
    expect(screen.getByText('Contact 1')).toBeInTheDocument()
    expect(screen.getByText('Contact 2')).toBeInTheDocument()
  })

  it('shows empty state when no conversations exist', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })
  })

  it('expands a conversation to show messages when clicked', async () => {
    const convos = makeConversations(1)
    const messages = [
      { id: 1, text: 'Hello from me', date: '2026-04-10T10:00:00+00:00', is_from_me: true, is_read: true, sender: 'me' },
      { id: 2, text: 'Hello back', date: '2026-04-10T10:01:00+00:00', is_from_me: false, is_read: true, sender: '+15550001234' },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/messages')) return Promise.resolve({ messages })
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: convos })
      return Promise.resolve({})
    })

    renderIMessage()

    const contactButton = await screen.findByText('Contact 0')
    fireEvent.click(contactButton)

    await waitFor(() => {
      expect(screen.getByText('Hello from me')).toBeInTheDocument()
    })
    expect(screen.getByText('Hello back')).toBeInTheDocument()
  })

  it('sends a new message via the composer', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      if (path.includes('/imessage/contacts/search')) return Promise.resolve({ contacts: [] })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('Send a message')).toBeInTheDocument()
    })

    const recipientInput = screen.getByTestId('contact-picker-input')
    const messageInput = screen.getByPlaceholderText('Type your message...')

    fireEvent.change(recipientInput, { target: { value: '+15550001234' } })
    fireEvent.change(messageInput, { target: { value: 'Test message' } })

    const sendButton = screen.getByRole('button', { name: 'Send' })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/imessage/send', {
        recipient: '+15550001234',
        text: 'Test message',
      })
    })
  })

  it('clears unread dot immediately when a conversation is opened', async () => {
    const convos = [
      {
        id: 1,
        identifier: '+15550001111',
        display_name: 'Alice',
        service: 'iMessage',
        last_message_date: '2026-04-10T10:00:00+00:00',
        last_message_preview: 'Hey',
        message_count: 5,
        unread_count: 3,
      },
    ]
    const messages = [
      { id: 1, text: 'Hey', date: '2026-04-10T10:00:00+00:00', is_from_me: false, is_read: false, sender: '+15550001111' },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/messages')) return Promise.resolve({ messages })
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: convos })
      return Promise.resolve({})
    })

    renderIMessage()

    await screen.findByText('Alice')
    expect(screen.getByText('3')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Alice'))

    await waitFor(() => {
      expect(screen.queryByText('3')).not.toBeInTheDocument()
    })
  })

  it('keeps unread dot cleared after a background refetch returns stale data', async () => {
    const convos = [
      {
        id: 1,
        identifier: '+15550001111',
        display_name: 'Alice',
        service: 'iMessage',
        last_message_date: '2026-04-10T10:00:00+00:00',
        last_message_preview: 'Hey',
        message_count: 5,
        unread_count: 3,
      },
    ]
    const messages = [
      { id: 1, text: 'Hey', date: '2026-04-10T10:00:00+00:00', is_from_me: false, is_read: false, sender: '+15550001111' },
    ]

    let conversationFetchCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/messages')) return Promise.resolve({ messages })
      if (path.includes('/imessage/conversations')) {
        conversationFetchCount++
        // Always return unread_count: 3 to simulate stale backend cache
        return Promise.resolve({ conversations: convos })
      }
      return Promise.resolve({})
    })

    renderIMessage()

    await screen.findByText('Alice')
    fireEvent.click(screen.getByText('Alice'))

    await waitFor(() => {
      expect(screen.queryByText('3')).not.toBeInTheDocument()
    })

    // Simulate window focus triggering a background refetch
    fireEvent.focus(window)

    await waitFor(() => {
      expect(conversationFetchCount).toBeGreaterThan(1)
    })

    // Dot must remain cleared even though backend returned stale unread_count: 3
    expect(screen.queryByText('3')).not.toBeInTheDocument()
  })

  it('shows search results when searching', async () => {
    const results = [
      {
        message_id: 1,
        text: 'Found this message',
        date: '2026-04-10T10:00:00+00:00',
        is_from_me: false,
        chat_id: 1,
        chat_identifier: '+15550001234',
        chat_display_name: 'Alice',
        sender: '+15550001234',
      },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations') && !path.includes('search')) {
        return Promise.resolve({ conversations: [] })
      }
      if (path.includes('/imessage/search')) return Promise.resolve({ results, query: 'hello' })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search messages...')).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText('Search messages...')
    fireEvent.change(searchInput, { target: { value: 'hello' } })
    fireEvent.keyDown(searchInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByText('Found this message')).toBeInTheDocument()
    })
    expect(screen.getByText('Alice')).toBeInTheDocument()
  })
})

describe('Contact picker in new-message composer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.imessageCache.v1')
    window.localStorage.removeItem('myos.imessageConnection.v1')
  })

  it('typing a partial name shows contact suggestions', async () => {
    const contacts = [
      { name: 'Alice Smith', phone: '+15550001234', email: null, identifier: '+15550001234' },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      if (path.includes('/imessage/contacts/search')) return Promise.resolve({ contacts })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => expect(screen.getByText('Send a message')).toBeInTheDocument())

    const recipientInput = screen.getByTestId('contact-picker-input')
    fireEvent.change(recipientInput, { target: { value: 'Ali' } })

    await waitFor(() => {
      expect(screen.getByText('Alice Smith')).toBeInTheDocument()
    })
    expect(screen.getByText('+15550001234')).toBeInTheDocument()
  })

  it('selecting a contact populates recipient with phone number, not display name', async () => {
    const contacts = [
      { name: 'Alice Smith', phone: '+15550001234', email: null, identifier: '+15550001234' },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      if (path.includes('/imessage/contacts/search')) return Promise.resolve({ contacts })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderIMessage()

    await waitFor(() => expect(screen.getByText('Send a message')).toBeInTheDocument())

    const recipientInput = screen.getByTestId('contact-picker-input')
    fireEvent.change(recipientInput, { target: { value: 'Ali' } })

    await waitFor(() => expect(screen.getByText('Alice Smith')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Alice Smith'))

    // Send a message and verify the raw phone number (not the display name) is sent
    const messageInput = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(messageInput, { target: { value: 'Hey' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/imessage/send', {
        recipient: '+15550001234',
        text: 'Hey',
      })
    })
  })

  it('raw phone number entry still works without contact selection', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      if (path.includes('/imessage/contacts/search')) return Promise.resolve({ contacts: [] })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderIMessage()

    await waitFor(() => expect(screen.getByText('Send a message')).toBeInTheDocument())

    const recipientInput = screen.getByTestId('contact-picker-input')
    fireEvent.change(recipientInput, { target: { value: '+15550001234' } })

    const messageInput = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(messageInput, { target: { value: 'Direct number' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/imessage/send', {
        recipient: '+15550001234',
        text: 'Direct number',
      })
    })
  })

  it('dropdown is hidden when search returns no results', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve(AVAILABLE_STATUS)
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: [] })
      if (path.includes('/imessage/contacts/search')) return Promise.resolve({ contacts: [] })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => expect(screen.getByText('Send a message')).toBeInTheDocument())

    const recipientInput = screen.getByTestId('contact-picker-input')
    fireEvent.change(recipientInput, { target: { value: 'zzznobody' } })

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(expect.stringContaining('/imessage/contacts/search'))
    })

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})

describe('IMessage ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.imessageCache.v1')
    window.localStorage.removeItem('myos.imessageConnection.v1')
  })

  it('renders ConnectCard with green accent when permissions are missing', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) {
        return Promise.resolve({ available: false, reason: 'iMessage database not found. This feature only works on macOS.' })
      }
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // Green accent color: #22c55e -> jsdom converts to rgb(34, 197, 94)
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/34, 197, 94/)
  })
})

describe('IMessage connection tri-state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.imessageCache.v1')
    window.localStorage.removeItem('myos.imessageConnection.v1')
  })

  it('first render shows LoadingState, not ConnectCard', () => {
    // Hang the status fetch so we can inspect the loading state
    let resolveStatus: (v: unknown) => void = () => {}
    const statusPromise = new Promise((resolve) => { resolveStatus = resolve })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return statusPromise as Promise<unknown>
      return Promise.resolve({})
    })

    renderIMessage()

    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
    expect(screen.queryByTestId('connect-card')).not.toBeInTheDocument()

    resolveStatus({ available: false, reason: 'nope' })
  })

  it('after fetch returns connected: true, the main UI renders', async () => {
    const convos = makeConversations(2)

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return Promise.resolve({ available: true, reason: null })
      if (path.includes('/imessage/conversations')) return Promise.resolve({ conversations: convos })
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('Contact 0')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('loading-state')).not.toBeInTheDocument()
  })

  it('after fetch returns connected: false, ConnectCard renders', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) {
        return Promise.resolve({ available: false, reason: 'iMessage not available on this machine.' })
      }
      return Promise.resolve({})
    })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('loading-state')).not.toBeInTheDocument()
  })

  it('return visit with localStorage=connected paints main UI immediately', async () => {
    // Seed localStorage to simulate a return visit where the user was connected
    window.localStorage.setItem('myos.imessageConnection.v1', 'connected')
    const convos = makeConversations(1)
    window.localStorage.setItem('myos.imessageCache.v1', JSON.stringify(convos))

    // Hang the status fetch to verify the page does NOT wait for it
    let resolveStatus: (v: unknown) => void = () => {}
    const statusPromise = new Promise((resolve) => { resolveStatus = resolve })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/imessage/status')) return statusPromise as Promise<unknown>
      return Promise.resolve({})
    })

    renderIMessage()

    // Should immediately show main UI seeded from cache, not loading spinner
    expect(screen.queryByTestId('loading-state')).not.toBeInTheDocument()
    expect(screen.queryByTestId('connect-card')).not.toBeInTheDocument()
    expect(screen.getByText('Contact 0')).toBeInTheDocument()

    resolveStatus({ available: true, reason: null })
  })
})
