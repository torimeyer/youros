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
