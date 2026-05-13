import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Contacts from './Contacts'

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

const SAMPLE_CONTACTS = [
  { name: 'Alice Smith', phone_numbers: ['+15550001234'], emails: ['alice@example.com'] },
  { name: 'Bob Jones', phone_numbers: ['5550005678'], emails: [] },
  { name: 'Carol', phone_numbers: [], emails: ['carol@work.com'] },
]

function renderContacts() {
  return render(
    <MemoryRouter>
      <Contacts />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Contacts page', () => {
  it('shows the privacy note', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => {
      expect(screen.getByTestId('privacy-note')).toBeInTheDocument()
    })
    expect(screen.getByTestId('privacy-note')).toHaveTextContent(
      'Stays on this machine'
    )
  })

  it('shows contact count after load', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => {
      expect(screen.getByText('3 contacts')).toBeInTheDocument()
    })
  })

  it('renders contact names', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => {
      expect(screen.getByText('Alice Smith')).toBeInTheDocument()
      expect(screen.getByText('Bob Jones')).toBeInTheDocument()
      expect(screen.getByText('Carol')).toBeInTheDocument()
    })
  })

  it('renders phone numbers and emails', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => {
      expect(screen.getByText('+15550001234')).toBeInTheDocument()
      expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })
  })

  it('filters contacts by name when searching', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => screen.getByText('Alice Smith'))

    const input = screen.getByPlaceholderText(/search/i)
    await userEvent.type(input, 'alice')

    expect(screen.getByText('Alice Smith')).toBeInTheDocument()
    expect(screen.queryByText('Bob Jones')).not.toBeInTheDocument()
    expect(screen.queryByText('Carol')).not.toBeInTheDocument()
  })

  it('shows empty state when search finds nothing', async () => {
    mockedApiGet.mockResolvedValue({ contacts: SAMPLE_CONTACTS, count: 3 })
    renderContacts()
    await waitFor(() => screen.getByText('Alice Smith'))

    const input = screen.getByPlaceholderText(/search/i)
    await userEvent.type(input, 'zzznomatch')

    expect(screen.queryByText('Alice Smith')).not.toBeInTheDocument()
    expect(screen.getByText(/no contacts matching/i)).toBeInTheDocument()
  })

  it('shows error banner on API failure', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderContacts()
    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeInTheDocument()
    })
  })

  it('shows loading spinner initially', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderContacts()
    expect(screen.getByText(/loading contacts/i)).toBeInTheDocument()
  })
})
