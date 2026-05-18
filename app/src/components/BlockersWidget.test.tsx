import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import BlockersWidget from './BlockersWidget'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
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
const mockedApiPost = vi.mocked(api.post)

const ONE_BLOCKER = {
  blockers: [
    {
      key: 'PROJ-42',
      summary: 'Blocked by infra change',
      status: 'Blocked',
      priority: 'High',
      url: 'https://acme.atlassian.net/browse/PROJ-42',
      updated: '2026-05-10T12:00:00.000+0000',
      age_days: 7,
      owners: ['Alice', 'Bob'],
    },
  ],
}

describe('BlockersWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the blockers list when data is returned', async () => {
    mockedApiGet.mockResolvedValueOnce(ONE_BLOCKER)
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blockers-list')).toBeInTheDocument()
    })
    expect(screen.getByText('Blocked by infra change')).toBeInTheDocument()
  })

  it('shows age_days for each blocker', async () => {
    mockedApiGet.mockResolvedValueOnce({
      blockers: [{ key: 'PROJ-42', summary: 'Stale blocker', status: 'Blocked', priority: 'High', url: '', updated: '', age_days: 16, owners: ['Carol'] }],
    })
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-age-PROJ-42')).toHaveTextContent('16d old')
    })
  })

  it('shows owners for each blocker', async () => {
    mockedApiGet.mockResolvedValueOnce({
      blockers: [{ key: 'PROJ-42', summary: 'Cross-team dep', status: 'In Progress', priority: 'Medium', url: '', updated: '', age_days: 3, owners: ['Dave', 'Eve'] }],
    })
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-owners-PROJ-42')).toHaveTextContent('Dave, Eve')
    })
  })

  it('renders enabled Nudge button for each row', async () => {
    mockedApiGet.mockResolvedValueOnce(ONE_BLOCKER)
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-nudge-PROJ-42')).toBeInTheDocument()
    })
    const nudgeBtn = screen.getByTestId('blocker-nudge-PROJ-42')
    expect(nudgeBtn).not.toBeDisabled()
    expect(nudgeBtn).toHaveTextContent('Send nudge')
  })

  it('shows channel picker when Nudge button is clicked', async () => {
    mockedApiGet.mockResolvedValueOnce(ONE_BLOCKER)
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-nudge-PROJ-42')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('blocker-nudge-PROJ-42'))
    expect(screen.getByTestId('nudge-picker-PROJ-42')).toBeInTheDocument()
    expect(screen.getByTestId('nudge-channel-jira')).toBeInTheDocument()
    expect(screen.getByTestId('nudge-channel-slack')).toBeInTheDocument()
  })

  it('calls POST /coordination/nudge and shows success on confirm', async () => {
    mockedApiGet.mockResolvedValueOnce(ONE_BLOCKER)
    mockedApiPost.mockResolvedValueOnce({ success: true, message_id: 'abc123' })
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-nudge-PROJ-42')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('blocker-nudge-PROJ-42'))
    fireEvent.click(screen.getByTestId('nudge-confirm-PROJ-42'))
    await waitFor(() => {
      expect(screen.getByTestId('nudge-result-PROJ-42')).toHaveTextContent('Nudge sent')
    })
    expect(mockedApiPost).toHaveBeenCalledWith(
      '/coordination/nudge',
      expect.objectContaining({ jira_key: 'PROJ-42' })
    )
  })

  it('shows error feedback when nudge POST returns success=false', async () => {
    mockedApiGet.mockResolvedValueOnce(ONE_BLOCKER)
    mockedApiPost.mockResolvedValueOnce({ success: false, error: 'Slack not connected' })
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blocker-nudge-PROJ-42')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('blocker-nudge-PROJ-42'))
    fireEvent.click(screen.getByTestId('nudge-confirm-PROJ-42'))
    await waitFor(() => {
      expect(screen.getByTestId('nudge-result-PROJ-42')).toHaveTextContent('Slack not connected')
    })
  })

  it('shows empty state when no blockers', async () => {
    mockedApiGet.mockResolvedValueOnce({ blockers: [] })
    render(<BlockersWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('blockers-empty')).toHaveTextContent('No cross-team blockers')
    })
  })
})
