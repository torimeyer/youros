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
const mockedApiPost = vi.mocked(api.post)
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

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SlackConnect disconnected state', () => {
  it('renders Access Token and App ID inputs when not connected', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: false })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => {
      expect(screen.getByTestId('slack-access-token-input')).toBeInTheDocument()
      expect(screen.getByTestId('slack-app-id-input')).toBeInTheDocument()
    })
    expect(screen.getByText(/Connect to Slack/i)).toBeInTheDocument()
  })

  it('posts the access token and app id to /slack/connect-token on submit', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: false })
    mockedApiPost.mockResolvedValue({ connected: true, team_id: 'T1', team_name: 'Acme' })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => screen.getByTestId('slack-access-token-input'))
    fireEvent.change(screen.getByTestId('slack-access-token-input'), { target: { value: 'xoxb-abc' } })
    fireEvent.change(screen.getByTestId('slack-app-id-input'), { target: { value: 'A001' } })
    fireEvent.click(screen.getByTestId('slack-connect-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/slack/connect-token', { access_token: 'xoxb-abc', app_id: 'A001' })
    })
  })

  it('does not submit when the access token is empty', async () => {
    mockedApiGet.mockResolvedValue({ connected: false, team_name: '', team_id: '', configured: false })

    render(<MemoryRouter><SlackConnect /></MemoryRouter>)

    await waitFor(() => screen.getByTestId('slack-connect-btn'))
    expect(screen.getByTestId('slack-connect-btn')).toBeDisabled()
  })
})

describe('SlackConnect connected state', () => {
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