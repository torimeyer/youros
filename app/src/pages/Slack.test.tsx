import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Slack from './Slack'

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

function renderSlack() {
  return render(
    <MemoryRouter>
      <Slack />
    </MemoryRouter>
  )
}

describe('Slack ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v1')
  })

  it('renders ConnectCard with purple accent when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // Purple accent color: #a855f7 -> jsdom converts to rgb(168, 85, 247)
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/168, 85, 247/)
  })

  it('shows Connect Slack title in ConnectCard', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: false })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByText('Connect Slack')).toBeInTheDocument()
    })
  })

  it('shows connect button when Slack is configured', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Slack workspace/i })).toBeInTheDocument()
    })
  })
})
