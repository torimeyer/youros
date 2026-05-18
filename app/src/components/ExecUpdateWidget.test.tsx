/**
 * RED tests for ExecUpdateWidget (→1437).
 * These fail until ExecUpdateWidget.tsx and ExecUpdateComposer.tsx exist.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

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
import ExecUpdateWidget from './ExecUpdateWidget'

const mockedApiGet = vi.mocked(api.get)

const MOCK_DRAFTS = [
  {
    draft_id: 'draft-001',
    audience: 'exec',
    window_days: 7,
    markdown: 'This week we shipped three major features. Performance improved by 20%.',
    source_refs: [],
    created_at: '2026-05-17T10:00:00Z',
  },
  {
    draft_id: 'draft-002',
    audience: 'team',
    window_days: 14,
    markdown: 'Roadmap update: we are on track for the Q2 milestone.',
    source_refs: [],
    created_at: '2026-05-16T10:00:00Z',
  },
  {
    draft_id: 'draft-003',
    audience: 'exec',
    window_days: 7,
    markdown: 'Last week summary with key decisions.',
    source_refs: [],
    created_at: '2026-05-15T10:00:00Z',
  },
]

describe('ExecUpdateWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockResolvedValue({ drafts: MOCK_DRAFTS })
  })

  it('renders the widget heading', async () => {
    render(<ExecUpdateWidget />)
    expect(screen.getByText(/exec update/i)).toBeTruthy()
  })

  it('renders at most 3 drafts from the API', async () => {
    render(<ExecUpdateWidget />)
    await waitFor(() => {
      // Each draft shows audience and first 200 chars of markdown
      expect(screen.getByText(/three major features/i)).toBeTruthy()
    })
    // Should not render more than 3 draft cards
    const draftItems = screen.queryAllByTestId(/draft-item-/)
    expect(draftItems.length).toBeLessThanOrEqual(3)
  })

  it('shows loading state while fetching', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {})) // never resolves
    render(<ExecUpdateWidget />)
    // Either a skeleton or loading indicator should appear
    const loading = screen.queryByTestId('exec-update-loading') ??
                    document.querySelector('[data-testid^="skeleton"]')
    expect(loading).toBeTruthy()
  })

  it('shows a "New update" button', async () => {
    render(<ExecUpdateWidget />)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /new update/i })).toBeTruthy()
    })
  })

  it('"New update" button opens the composer modal', async () => {
    render(<ExecUpdateWidget />)
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /new update/i })
      fireEvent.click(btn)
    })
    await waitFor(() => {
      // Composer modal should be visible
      expect(screen.getByRole('dialog', { name: /new exec update/i })).toBeTruthy()
    })
  })

  it('shows error state when fetch fails', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    render(<ExecUpdateWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('exec-update-error')).toBeTruthy()
    })
  })

  it('shows empty state when no drafts exist', async () => {
    mockedApiGet.mockResolvedValue({ drafts: [] })
    render(<ExecUpdateWidget />)
    await waitFor(() => {
      expect(screen.getByTestId('exec-update-empty')).toBeTruthy()
    })
  })
})
