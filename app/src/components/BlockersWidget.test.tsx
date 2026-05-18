import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import BlockersWidget from './BlockersWidget'

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

describe('BlockersWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the blockers list when data is returned', async () => {
    mockedApiGet.mockResolvedValueOnce({
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
    })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByTestId('blockers-list')).toBeInTheDocument()
    })

    expect(screen.getByText('Blocked by infra change')).toBeInTheDocument()
  })

  it('shows age_days for each blocker', async () => {
    mockedApiGet.mockResolvedValueOnce({
      blockers: [
        {
          key: 'PROJ-42',
          summary: 'Stale blocker',
          status: 'Blocked',
          priority: 'High',
          url: 'https://acme.atlassian.net/browse/PROJ-42',
          updated: '2026-05-01T00:00:00.000+0000',
          age_days: 16,
          owners: ['Carol'],
        },
      ],
    })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByTestId('blocker-age-PROJ-42')).toBeInTheDocument()
    })
    expect(screen.getByTestId('blocker-age-PROJ-42')).toHaveTextContent('16d old')
  })

  it('shows owners for each blocker', async () => {
    mockedApiGet.mockResolvedValueOnce({
      blockers: [
        {
          key: 'PROJ-99',
          summary: 'Cross-team dep',
          status: 'In Progress',
          priority: 'Medium',
          url: 'https://acme.atlassian.net/browse/PROJ-99',
          updated: '2026-05-15T00:00:00.000+0000',
          age_days: 2,
          owners: ['Carol', 'Dave'],
        },
      ],
    })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByTestId('blocker-owners-PROJ-99')).toBeInTheDocument()
    })
    expect(screen.getByTestId('blocker-owners-PROJ-99')).toHaveTextContent('Carol')
    expect(screen.getByTestId('blocker-owners-PROJ-99')).toHaveTextContent('Dave')
  })

  it('renders Nudge button disabled for each row', async () => {
    mockedApiGet.mockResolvedValueOnce({
      blockers: [
        {
          key: 'PROJ-55',
          summary: 'Some blocker',
          status: 'Blocked',
          priority: 'High',
          url: 'https://acme.atlassian.net/browse/PROJ-55',
          updated: '2026-05-12T00:00:00.000+0000',
          age_days: 5,
          owners: ['Eve'],
        },
      ],
    })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByTestId('blocker-nudge-PROJ-55')).toBeInTheDocument()
    })
    const nudgeBtn = screen.getByTestId('blocker-nudge-PROJ-55')
    expect(nudgeBtn).toBeDisabled()
    expect(nudgeBtn).toHaveTextContent('Nudge')
  })

  it('shows empty state when no blockers', async () => {
    mockedApiGet.mockResolvedValueOnce({ blockers: [] })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByTestId('blockers-empty')).toBeInTheDocument()
    })
    expect(screen.getByTestId('blockers-empty')).toHaveTextContent('No cross-team blockers')
  })
})
