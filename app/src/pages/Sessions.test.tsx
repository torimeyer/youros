import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sessions from './Sessions'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({ api: { get: vi.fn() } }))
const mockedGet = vi.mocked(api.get)

function makeSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'claude-code-p12345',
    name: 'claude-code-p12345',
    label: 'Building the sessions page',
    type: 'claude-code',
    started_at: null,
    last_active_at: new Date(Date.now() - 20000).toISOString(),
    status: 'active' as const,
    activity: 'Running command: pytest api/tests/ -x',
    recent_files: ['api/routers/sessions.py', 'app/src/pages/Sessions.tsx'],
    stuck: false,
    ...overrides,
  }
}

function makePayload(sessions: ReturnType<typeof makeSession>[] = [makeSession()]) {
  return { sessions, locks: [], events: [] }
}

beforeAll(() => {
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
})

function renderPage() {
  return render(
    <MemoryRouter>
      <Sessions />
    </MemoryRouter>
  )
}

describe('Sessions page', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedGet.mockResolvedValue(makePayload() as never)
  })

  it('renders the sessions column', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeTruthy()
    })
  })

  it('shows the session label', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Building the sessions page')).toBeTruthy()
    })
  })

  it('shows the activity line', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Running command: pytest/)).toBeTruthy()
    })
  })

  it('shows recent files', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/sessions\.py/)).toBeTruthy()
    })
  })

  it('does not show stuck badge when stuck=false', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeTruthy()
    })
    expect(screen.queryByTestId('stuck-badge')).toBeNull()
  })

  it('shows stuck badge when stuck=true', async () => {
    mockedGet.mockResolvedValue(makePayload([makeSession({ stuck: true })]) as never)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('stuck-badge')).toBeTruthy()
    })
  })

  it('falls back gracefully when enriched fields are absent', async () => {
    mockedGet.mockResolvedValue(makePayload([{
      id: 'old-format-session',
      name: 'old-format-session',
      type: 'ostk',
      started_at: null,
      last_active_at: new Date().toISOString(),
      status: 'active' as const,
    } as never]) as never)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeTruthy()
    })
    expect(screen.queryByTestId('stuck-badge')).toBeNull()
  })
})
