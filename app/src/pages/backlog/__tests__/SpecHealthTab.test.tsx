import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SpecHealthTab from '../SpecHealthTab'

vi.mock('../../../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../../../lib/api'
const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

// Dates relative to 2026-05-17 (today)
const FRESH_DATE = '2026-04-01T00:00:00Z'  // 46 days ago — not stale
const STALE_DATE = '2026-02-05T00:00:00Z'  // 101 days ago — stale (>90)

const SAMPLE_AUDIT = {
  specs: [
    {
      path: '~/.myos/specs/auth-module.md',
      frontmatter: { title: 'Auth module', status: 'spec' },
      sections_present: ['Problem', 'Goals', 'Solution', 'Verification', 'DECISION'],
      sections_missing: ['Non-goals', 'Edge cases', 'Success criteria', 'Acceptance criteria', 'USER FEEDBACK'],
      score: 5,
      last_edited: FRESH_DATE,
    },
    {
      path: 'docs/spec/dashboard.md',
      frontmatter: { title: 'Dashboard redesign', status: 'draft' },
      sections_present: ['Problem'],
      sections_missing: ['Goals', 'Solution', 'Non-goals', 'Edge cases', 'Success criteria', 'Acceptance criteria', 'Verification', 'USER FEEDBACK', 'DECISION'],
      score: 1,
      last_edited: STALE_DATE,
    },
  ],
  summary: { total: 2, fully_templated: 0, partial: 2, score_avg: 3.0 },
}

function renderTab() {
  return render(
    <MemoryRouter>
      <SpecHealthTab />
    </MemoryRouter>
  )
}

describe('SpecHealthTab (→1471)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedGet.mockResolvedValue(SAMPLE_AUDIT)
    mockedPost.mockResolvedValue({})
  })

  it('renders rows from mocked audit response', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('Auth module')).toBeInTheDocument()
      expect(screen.getByText('Dashboard redesign')).toBeInTheDocument()
    })
  })

  it('shows location column (myos vs docs/spec)', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('~/.myos/specs/')).toBeInTheDocument()
      expect(screen.getByText('docs/spec/')).toBeInTheDocument()
    })
  })

  it('shows score for each spec', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('5/10')).toBeInTheDocument()
      expect(screen.getByText('1/10')).toBeInTheDocument()
    })
  })

  it('shows status for each spec', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('spec')).toBeInTheDocument()
      expect(screen.getByText('draft')).toBeInTheDocument()
    })
  })

  it('Backfill button triggers POST to /api/specs/{slug}/backfill', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /backfill/i }).length).toBeGreaterThan(0)
    })
    fireEvent.click(screen.getAllByRole('button', { name: /backfill/i })[0])
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/specs/auth-module/backfill', {})
    })
  })

  it('Archive button triggers POST to /api/specs/{slug}/archive', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /archive/i }).length).toBeGreaterThan(0)
    })
    fireEvent.click(screen.getAllByRole('button', { name: /archive/i })[0])
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/specs/auth-module/archive', {})
    })
  })

  it('shows Stale badge when last_edited is older than 90 days', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('Dashboard redesign')).toBeInTheDocument()
    })
    expect(screen.getByText('Stale')).toBeInTheDocument()
  })

  it('does NOT show Stale badge for recently edited specs', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('Auth module')).toBeInTheDocument()
    })
    const staleBadges = screen.queryAllByText('Stale')
    expect(staleBadges).toHaveLength(1)
  })

  it('renders missing-sections list for low-score specs', async () => {
    renderTab()
    await waitFor(() => {
      expect(screen.getByText('Dashboard redesign')).toBeInTheDocument()
    })
    expect(screen.getByText('Goals')).toBeInTheDocument()
  })
})
