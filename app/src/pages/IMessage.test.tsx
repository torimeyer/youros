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

    expect(screen.getByText('Loading...')).toBeInTheDocument()

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
      expect(screen.getByText('No conversations found.')).toBeInTheDocument()
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
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderIMessage()

    await waitFor(() => {
      expect(screen.getByText('Send a message')).toBeInTheDocument()
    })

    const recipientInput = screen.getByPlaceholderText('Phone number or email')
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
