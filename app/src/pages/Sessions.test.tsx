import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sessions from './Sessions'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

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
const mockedGet = vi.mocked(api.get)

const fixtureData = {
  sessions: [
    {
      id: 'agent-builder-abc',
      name: 'Build the widget',
      type: 'agent',
      started_at: '2026-04-27T10:00:00Z',
      last_active_at: '2026-04-27T10:05:00Z',
      status: 'active' as const,
    },
    {
      id: 'claude-code-xyz',
      name: 'claude-code-xyz',
      type: 'claude-code',
      started_at: null,
      last_active_at: '2026-04-27T09:55:00Z',
      status: 'idle' as const,
    },
  ],
  locks: [
    {
      lock_name: 'needle-924',
      held_by_session: 'agent-builder-abc',
      started_at: '2026-04-27T10:00:00Z',
      paths: ['app/src/pages/Sessions.tsx'],
    },
  ],
  events: [
    {
      from_session: 'user',
      to_session: 'agent-builder-abc',
      message: 'please implement sessions panel',
      timestamp: '2026-04-27T10:00:00Z',
      kind: 'user_message',
    },
  ],
}

beforeEach(() => {
  mockedGet.mockResolvedValue(fixtureData)
})

describe('Sessions page', () => {
  it('renders all three column headers', async () => {
    render(
      <MemoryRouter>
        <Sessions />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeInTheDocument()
      expect(screen.getByTestId('locks-column')).toBeInTheDocument()
      expect(screen.getByTestId('events-column')).toBeInTheDocument()
    })

    expect(screen.getByText(/active sessions/i)).toBeInTheDocument()
    expect(screen.getByText(/locks held/i)).toBeInTheDocument()
    expect(screen.getByText(/recent coordination/i)).toBeInTheDocument()
  })

  it('displays fixture session rows', async () => {
    render(
      <MemoryRouter>
        <Sessions />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getAllByTestId('session-row')).toHaveLength(2)
    })

    expect(screen.getByText('Build the widget')).toBeInTheDocument()
  })

  it('displays fixture lock', async () => {
    render(
      <MemoryRouter>
        <Sessions />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('lock-row')).toBeInTheDocument()
    })

    expect(screen.getByText('needle-924')).toBeInTheDocument()
  })

  it('displays fixture coordination event', async () => {
    render(
      <MemoryRouter>
        <Sessions />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('event-row')).toBeInTheDocument()
    })

    expect(screen.getByText('please implement sessions panel')).toBeInTheDocument()
  })

  it('shows empty states when all arrays are empty', async () => {
    mockedGet.mockResolvedValue({ sessions: [], locks: [], events: [] })

    render(
      <MemoryRouter>
        <Sessions />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/no sessions right now/i)).toBeInTheDocument()
      expect(screen.getByText(/no locks held/i)).toBeInTheDocument()
      expect(screen.getByText(/no recent messages/i)).toBeInTheDocument()
    })
  })
})
