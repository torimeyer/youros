import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import IMessage from './IMessage'

// jsdom does not provide window.matchMedia — stub it so TopBar doesn't crash
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

const mockConversations = [
  {
    id: 1,
    identifier: '+14155550101',
    display_name: 'Jen Wilson',
    has_contact_name: true,
    service: 'iMessage',
    last_message_date: '2026-01-01T10:00:00Z',
    last_message_preview: 'Hey!',
    message_count: 5,
    unread_count: 1,
  },
  {
    id: 2,
    identifier: '+14085550202',
    display_name: 'Bob Smith',
    has_contact_name: true,
    service: 'SMS',
    last_message_date: '2026-01-01T09:00:00Z',
    last_message_preview: 'Ok',
    message_count: 2,
    unread_count: 0,
  },
]

const mockContacts = [
  { name: 'Jennifer Brown', phone_numbers: ['+14155550303'], emails: ['jen@example.com'] },
  { name: 'Bob Jones', phone_numbers: ['+14085550404'], emails: [] },
]

function setup() {
  mockApi.get.mockImplementation((path: string) => {
    if (path === '/imessage/status') return Promise.resolve({ available: true, reason: null })
    if (path === '/imessage/conversations') return Promise.resolve({ conversations: mockConversations })
    if (path === '/contacts') return Promise.resolve({ contacts: mockContacts })
    return Promise.resolve({})
  })
  return render(
    <MemoryRouter>
      <IMessage />
    </MemoryRouter>
  )
}

const CACHE_KEY = 'myos.imessageCache.v1'
const CONNECTION_KEY = 'myos.imessageConnection.v1'

describe('connection state — bug fixes (→1577)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('ignores stale not_connected cache and shows conversations when API returns available', async () => {
    localStorage.setItem(CONNECTION_KEY, 'not_connected')
    setup()
    await waitFor(() => expect(screen.getByText('Jen Wilson')).toBeTruthy())
    expect(screen.queryByText('iMessage not available')).toBeNull()
  })

  it('renders cached conversations immediately while status check is in-flight', async () => {
    localStorage.setItem(CACHE_KEY, JSON.stringify(mockConversations))
    let resolveStatus!: (v: unknown) => void
    mockApi.get.mockImplementation((path: string) => {
      if (path === '/imessage/status') return new Promise(r => { resolveStatus = r })
      if (path === '/imessage/conversations') return Promise.resolve({ conversations: mockConversations })
      if (path === '/contacts') return Promise.resolve({ contacts: mockContacts })
      return Promise.resolve({})
    })
    render(
      <MemoryRouter>
        <IMessage />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('Jen Wilson')).toBeTruthy())
    resolveStatus({ available: true, reason: null })
  })
})

describe('People page — unified contact picker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('renders the People heading', async () => {
    setup()
    await waitFor(() => expect(screen.getByText('People')).toBeTruthy())
  })

  it('shows contacts matching a name query in the send-message picker', async () => {
    setup()
    await waitFor(() => screen.getByTestId('contact-picker-input'))
    fireEvent.change(screen.getByTestId('contact-picker-input'), { target: { value: 'jen' } })
    await waitFor(() => {
      const rows = screen.getAllByTestId('contact-picker-contact-row')
      expect(rows.some((r) => r.textContent?.includes('Jennifer Brown'))).toBe(true)
    })
  })

  it('shows conversation threads matching a name query in the send-message picker', async () => {
    setup()
    await waitFor(() => screen.getByTestId('contact-picker-input'))
    fireEvent.change(screen.getByTestId('contact-picker-input'), { target: { value: 'jen' } })
    await waitFor(() => {
      const rows = screen.getAllByTestId('contact-picker-convo-row')
      expect(rows.some((r) => r.textContent?.includes('Jen Wilson'))).toBe(true)
    })
  })

  it('shows threads matching a phone area code in the send-message picker', async () => {
    setup()
    await waitFor(() => screen.getByTestId('contact-picker-input'))
    fireEvent.change(screen.getByTestId('contact-picker-input'), { target: { value: '+1415' } })
    await waitFor(() => {
      const rows = screen.getAllByTestId('contact-picker-convo-row')
      expect(rows.some((r) => r.textContent?.includes('Jen Wilson'))).toBe(true)
    })
  })

  it('shows no results when nothing matches', async () => {
    setup()
    await waitFor(() => screen.getByTestId('contact-picker-input'))
    fireEvent.change(screen.getByTestId('contact-picker-input'), { target: { value: 'zzznomatch' } })
    // No rows should appear for this query
    await waitFor(() => {
      expect(screen.queryByTestId('contact-picker-contact-row')).toBeNull()
      expect(screen.queryByTestId('contact-picker-convo-row')).toBeNull()
    })
  })

  it('does not show a top-level people-search-input field', async () => {
    setup()
    await waitFor(() => screen.getByText('People'))
    expect(screen.queryByTestId('people-search-input')).toBeNull()
  })
})

describe('message thread — bubbles (→1632)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  function setupWithMessages(msgs: Array<{
    id: number; text: string; date: string
    is_from_me: boolean; is_read: boolean; sender: string; attachments: unknown[]
  }>) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === '/imessage/status') return Promise.resolve({ available: true, reason: null })
      if (path === '/imessage/conversations') return Promise.resolve({ conversations: mockConversations })
      if (path === '/contacts') return Promise.resolve({ contacts: mockContacts })
      if (/\/imessage\/conversations\/\d+\/messages/.test(path)) return Promise.resolve({ messages: msgs })
      return Promise.resolve({})
    })
    return render(
      <MemoryRouter>
        <IMessage />
      </MemoryRouter>
    )
  }

  it('renders message body text in the bubble (Bug A)', async () => {
    setupWithMessages([
      { id: 1, text: 'Hello there', date: new Date().toISOString(), is_from_me: false, is_read: true, sender: '+14155550101', attachments: [] },
    ])
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => expect(screen.getByText('Hello there')).toBeTruthy())
  })

  it('does not render a text bubble for attachment-only ￼ messages (Bug A)', async () => {
    setupWithMessages([
      { id: 1, text: '￼', date: new Date().toISOString(), is_from_me: false, is_read: true, sender: '+14155550101', attachments: [] },
      { id: 2, text: 'Marker', date: new Date().toISOString(), is_from_me: true, is_read: true, sender: 'me', attachments: [] },
    ])
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => screen.getByText('Marker'))
    expect(screen.queryByText('￼')).toBeNull()
  })

  it('formats message timestamps using browser local timezone, not UTC (Bug B)', async () => {
    const msgDate = new Date(Date.now() - 30 * 60 * 1000).toISOString()
    setupWithMessages([
      { id: 1, text: 'Time check', date: msgDate, is_from_me: false, is_read: true, sender: '+14155550101', attachments: [] },
    ])
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => screen.getByText('Time check'))
    const expectedTime = new Date(msgDate).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    expect(screen.getByText(expectedTime)).toBeTruthy()
  })
})

describe('attachment rendering (→1633)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  const mockMessagesWithImage = [
    {
      id: 10,
      text: '',
      date: '2026-01-01T10:00:00Z',
      is_from_me: false,
      is_read: true,
      sender: '+14155550101',
      attachments: [
        {
          filename: '/Users/test/Library/Messages/Attachments/00/IMG_2328.HEIC',
          mime_type: 'image/heic',
          transfer_name: 'IMG_2328.HEIC',
        },
      ],
    },
  ]

  function setupWithMessages() {
    mockApi.get.mockImplementation((path: string) => {
      if (path === '/imessage/status') return Promise.resolve({ available: true, reason: null })
      if (path === '/imessage/conversations') return Promise.resolve({ conversations: mockConversations })
      if (path === '/contacts') return Promise.resolve({ contacts: [] })
      if (path.startsWith('/imessage/conversations/1/messages'))
        return Promise.resolve({ messages: mockMessagesWithImage })
      return Promise.resolve({})
    })
    return render(
      <MemoryRouter>
        <IMessage />
      </MemoryRouter>
    )
  }

  it('renders an img tag for image attachments', async () => {
    setupWithMessages()
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => {
      const img = screen.queryByTestId('attachment-image')
      expect(img).not.toBeNull()
    })
  })

  it('img src points to the attachment endpoint', async () => {
    setupWithMessages()
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => {
      const img = screen.getByTestId('attachment-image') as HTMLImageElement
      expect(img.src).toContain('/api/imessage/attachment?path=')
      expect(img.src).toContain('IMG_2328.HEIC')
    })
  })

  it('shows placeholder when image fails to load', async () => {
    setupWithMessages()
    await waitFor(() => screen.getByText('Jen Wilson'))
    fireEvent.click(screen.getByText('Jen Wilson'))
    await waitFor(() => screen.getByTestId('attachment-image'))
    const img = screen.getByTestId('attachment-image')
    fireEvent.error(img)
    await waitFor(() => {
      expect(screen.queryByTestId('attachment-image')).toBeNull()
      const placeholder = screen.getByTestId('attachment-placeholder')
      expect(placeholder).toBeTruthy()
      expect(placeholder.textContent).toContain('IMG_2328.HEIC')
    })
  })
})
