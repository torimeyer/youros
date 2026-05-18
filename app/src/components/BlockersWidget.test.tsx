import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import BlockersWidget from './BlockersWidget'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
    },
  }
})

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

describe('BlockersWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders blockers list with age and owners', async () => {
    mockedApiGet.mockResolvedValue({
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
      expect(screen.getByTestId('widget-blockers')).toBeInTheDocument()
    })

    expect(screen.getByText('Blocked by infra change')).toBeInTheDocument()
    expect(screen.getByText(/7d old/)).toBeInTheDocument()
    expect(screen.getByText(/Alice/)).toBeInTheDocument()
  })

  it('renders Nudge button that is disabled', async () => {
    mockedApiGet.mockResolvedValue({
      blockers: [
        {
          key: 'PROJ-1',
          summary: 'Cross-team blocker',
          status: 'Blocked',
          priority: 'Medium',
          url: 'https://acme.atlassian.net/browse/PROJ-1',
          updated: '2026-05-15T00:00:00.000+0000',
          age_days: 2,
          owners: ['Carol'],
        },
      ],
    })

    render(<BlockersWidget />)

    await waitFor(() => {
      const nudgeBtn = screen.getByRole('button', { name: /nudge/i })
      expect(nudgeBtn).toBeInTheDocument()
      expect(nudgeBtn).toBeDisabled()
    })
  })

  it('shows empty state when no blockers', async () => {
    mockedApiGet.mockResolvedValue({ blockers: [] })

    render(<BlockersWidget />)

    await waitFor(() => {
      expect(screen.getByText(/no blockers/i)).toBeInTheDocument()
    })
  })
})
