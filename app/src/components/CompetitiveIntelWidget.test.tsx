import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import CompetitiveIntelWidget from './CompetitiveIntelWidget'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

const SAMPLE_CAPTURES = [
  {
    id: 'c1',
    competitor: 'Acme Corp',
    tags: ['pricing'],
    url: 'https://acme.com/blog',
    text: null,
    captured_at: '2026-05-17T10:00:00Z',
  },
  {
    id: 'c2',
    competitor: 'Rival Co',
    tags: ['feature', 'launch'],
    url: null,
    text: 'They announced a free tier with 5 seats.',
    captured_at: '2026-05-16T09:00:00Z',
  },
]

describe('CompetitiveIntelWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockResolvedValue({ captures: [] })
  })

  it('renders the widget container', async () => {
    render(<CompetitiveIntelWidget />)
    expect(screen.getByTestId('widget-competitive-intel')).toBeInTheDocument()
  })

  it('shows empty state when no captures', async () => {
    mockApi.get.mockResolvedValue({ captures: [] })
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('intel-empty-state')).toBeInTheDocument()
    })
  })

  it('renders up to 5 captures', async () => {
    mockApi.get.mockResolvedValue({ captures: SAMPLE_CAPTURES })
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      const rows = screen.getAllByTestId('intel-capture-row')
      expect(rows).toHaveLength(2)
    })
  })

  it('shows competitor names', async () => {
    mockApi.get.mockResolvedValue({ captures: SAMPLE_CAPTURES })
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      expect(screen.getByText('Acme Corp')).toBeInTheDocument()
      expect(screen.getByText('Rival Co')).toBeInTheDocument()
    })
  })

  it('shows tags for captures', async () => {
    mockApi.get.mockResolvedValue({ captures: SAMPLE_CAPTURES })
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      expect(screen.getByText('pricing')).toBeInTheDocument()
      expect(screen.getByText('feature')).toBeInTheDocument()
    })
  })

  it('shows error state when api fails', async () => {
    mockApi.get.mockRejectedValue(new Error('network fail'))
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      expect(screen.getByText(/could not load/i)).toBeInTheDocument()
    })
  })

  it('shows Add button', async () => {
    render(<CompetitiveIntelWidget />)
    await waitFor(() => {
      expect(screen.getByLabelText('Add competitor signal')).toBeInTheDocument()
    })
  })
})
