import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sessions from './Sessions'
import { api } from '../lib/api'

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

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const mockedApiGet = vi.mocked(api.get)

const EMPTY_COORDINATION = {
  sessions: [],
  locks: [],
  events: [],
  conflicts: [],
}

describe('Sessions page - ConflictsStrip', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders conflicts strip with file name and session labels when conflicts present', async () => {
    mockedApiGet.mockResolvedValue({
      ...EMPTY_COORDINATION,
      conflicts: [
        {
          path: 'src/api/routes.py',
          session_ids: ['session-alice', 'session-bob'],
          last_write_times: {
            'session-alice': new Date().toISOString(),
            'session-bob': new Date().toISOString(),
          },
        },
      ],
    })

    render(<MemoryRouter><Sessions /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('conflicts-strip')).toBeInTheDocument()
    })

    expect(screen.getByTestId('conflict-row')).toBeInTheDocument()
    expect(screen.getByTestId('conflict-path')).toHaveTextContent('routes.py')
    expect(screen.getByTestId('conflict-sessions')).toHaveTextContent('session-alice')
    expect(screen.getByTestId('conflict-sessions')).toHaveTextContent('session-bob')
  })

  it('does not render conflicts strip when conflicts list is empty', async () => {
    mockedApiGet.mockResolvedValue(EMPTY_COORDINATION)

    render(<MemoryRouter><Sessions /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('conflicts-strip')).not.toBeInTheDocument()
  })

  it('does not render conflicts strip when conflicts field is absent', async () => {
    mockedApiGet.mockResolvedValue({
      sessions: [],
      locks: [],
      events: [],
    })

    render(<MemoryRouter><Sessions /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('sessions-column')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('conflicts-strip')).not.toBeInTheDocument()
  })

  it('renders multiple conflict rows when multiple paths conflict', async () => {
    mockedApiGet.mockResolvedValue({
      ...EMPTY_COORDINATION,
      conflicts: [
        {
          path: 'src/app.py',
          session_ids: ['session-a', 'session-b'],
          last_write_times: {},
        },
        {
          path: 'src/utils.py',
          session_ids: ['session-a', 'session-c'],
          last_write_times: {},
        },
      ],
    })

    render(<MemoryRouter><Sessions /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('conflicts-strip')).toBeInTheDocument()
    })

    const rows = screen.getAllByTestId('conflict-row')
    expect(rows).toHaveLength(2)
    expect(screen.getByText('app.py')).toBeInTheDocument()
    expect(screen.getByText('utils.py')).toBeInTheDocument()
  })
})
