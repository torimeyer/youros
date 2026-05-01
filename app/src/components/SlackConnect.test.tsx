import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SlackConnect from './SlackConnect'

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiDelete = vi.mocked(api.delete)

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Capture window.location.href assignments
const locationHrefSetter = vi.fn()
Object.defineProperty(window, 'location', {
  value: { ...window.location, set href(v: string) { locationHrefSetter(v) } },
  writable: true,
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SlackConnect — disconnected state', () => {
  it('renders "Connect your Slack workspace" button when not connected', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('slack-connect-btn')).toBeInTheDocument()
    })
    expect(screen.getByText(/Connect your Slack workspace/i)).toBeInTheDocument()
  })

  it('navigates to /api/auth/slack/login when button is clicked', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => screen.getByTestId('slack-connect-btn'))
    fireEvent.click(screen.getByTestId('slack-connect-btn'))

    expect(locationHrefSetter).toHaveBeenCalledWith('/api/auth/slack/login')
  })

  it('shows setup prompt when configured is false', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: false })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByText(/Set up Slack credentials first/i)).toBeInTheDocument()
    })
  })

  it('shows "One-click sign in" when configured is true', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByText(/One-click sign in via Slack OAuth/i)).toBeInTheDocument()
    })
  })
})

describe('SlackConnect — connected state', () => {
  it('shows workspace name when connected', async () => {
    mockedApiGet.mockResolvedValue({ connected: true, team_name: 'Acme Corp', team_id: 'T1', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('slack-connect-connected')).toBeInTheDocument()
    })
    expect(screen.getByText(/Connected to Acme Corp/i)).toBeInTheDocument()
  })

  it('renders Disconnect button when connected', async () => {
    mockedApiGet.mockResolvedValue({ connected: true, team_name: 'Acme', team_id: 'T2', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => screen.getByTestId('slack-disconnect-btn'))
    expect(screen.getByTestId('slack-disconnect-btn')).toBeInTheDocument()
  })

  it('calls /slack/disconnect when Disconnect is clicked', async () => {
    mockedApiGet.mockResolvedValue({ connected: true, team_name: 'Acme', team_id: 'T2', configured: true })
    mockedApiDelete.mockResolvedValue({})

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => screen.getByTestId('slack-disconnect-btn'))
    fireEvent.click(screen.getByTestId('slack-disconnect-btn'))

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/slack/disconnect')
    })
  })

  it('shows fallback workspace label when team_name is empty', async () => {
    mockedApiGet.mockResolvedValue({ connected: true, team_name: '', team_id: 'T3', configured: true })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByText(/Connected to your workspace/i)).toBeInTheDocument()
    })
  })
})
