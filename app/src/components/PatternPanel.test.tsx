import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PatternPanel from './PatternPanel'

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}))

const CLUSTER_TASK = {
  id: 'abc123',
  kind: 'task:defer',
  label: 'You frequently defer tasks',
  count: 5,
  last_seen: '2h ago',
  tier: 1,
}

const CLUSTER_VOCAB = {
  id: 'def456',
  kind: 'vocab:new',
  label: 'You use the term "elit"',
  count: 3,
  last_seen: '1d ago',
  tier: 1,
}

describe('PatternPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPost.mockResolvedValue({})
  })

  it('shows empty state when no clusters exist', async () => {
    mockGet.mockResolvedValue({ clusters: [] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('pattern-panel-empty')).toBeDefined()
    })
  })

  it('fetches clusters from /patterns/clusters — the api client adds the /api prefix (→2887)', async () => {
    // Regression: passing "/api/patterns/clusters" here double-prefixed to
    // "/api/api/patterns/clusters", a 404 that made the What I learned tab
    // permanently empty.
    mockGet.mockResolvedValue({ clusters: [] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/patterns/clusters')
    })
  })

  it('renders clusters with label and seen count', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('pattern-panel')).toBeDefined()
      expect(screen.getByText('You frequently defer tasks')).toBeDefined()
      expect(screen.getByText(/seen 5/)).toBeDefined()
    })
  })

  it('shows Confirm and Dismiss buttons for tier=1 clusters', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('confirm-abc123')).toBeDefined()
      expect(screen.getByTestId('dismiss-abc123')).toBeDefined()
    })
  })

  it('clicking Confirm posts tier=2 to the clusters endpoint', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK] })
    render(<PatternPanel />)
    await waitFor(() => screen.getByTestId('confirm-abc123'))

    fireEvent.click(screen.getByTestId('confirm-abc123'))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/patterns/clusters/abc123/tier',
        { tier: 2 },
      )
    })
  })

  it('clicking Dismiss posts tier=0 and hides the cluster', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK] })
    render(<PatternPanel />)
    await waitFor(() => screen.getByTestId('dismiss-abc123'))

    fireEvent.click(screen.getByTestId('dismiss-abc123'))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/patterns/clusters/abc123/tier',
        { tier: 0 },
      )
    })
    // cluster should no longer appear in the panel
    await waitFor(() => {
      expect(screen.queryByTestId('pattern-cluster-abc123')).toBeNull()
    })
  })

  it('shows approve-silent button after confirming (tier=2)', async () => {
    mockGet.mockResolvedValue({ clusters: [{ ...CLUSTER_TASK, tier: 2 }] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('pattern-approve-silent')).toBeDefined()
    })
  })

  it('clicking Approve for silent action posts tier=3 to the clusters endpoint', async () => {
    mockGet.mockResolvedValue({ clusters: [{ ...CLUSTER_TASK, tier: 2 }] })
    render(<PatternPanel />)
    await waitFor(() => screen.getByTestId('pattern-approve-silent'))

    fireEvent.click(screen.getByTestId('pattern-approve-silent'))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/patterns/clusters/abc123/tier',
        { tier: 3 },
      )
    })
  })

  it('shows "Silent action enabled" badge and hides approve button after tier=3', async () => {
    mockGet.mockResolvedValue({ clusters: [{ ...CLUSTER_TASK, tier: 2 }] })
    render(<PatternPanel />)
    await waitFor(() => screen.getByTestId('pattern-approve-silent'))

    fireEvent.click(screen.getByTestId('pattern-approve-silent'))
    await waitFor(() => {
      expect(screen.queryByTestId('pattern-approve-silent')).toBeNull()
      expect(screen.getByText('Silent action enabled')).toBeDefined()
    })
  })

  it('full flow: tier=1 → confirm → tier=2 → approve silent → tier=3 with audit badge', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK] })
    render(<PatternPanel />)
    await waitFor(() => screen.getByTestId('confirm-abc123'))

    // Step 1: Confirm (tier 1 → 2)
    fireEvent.click(screen.getByTestId('confirm-abc123'))
    await waitFor(() => screen.getByTestId('pattern-approve-silent'))

    // Step 2: Approve for silent action (tier 2 → 3)
    fireEvent.click(screen.getByTestId('pattern-approve-silent'))
    await waitFor(() => {
      expect(screen.getByText('Silent action enabled')).toBeDefined()
      expect(mockPost).toHaveBeenLastCalledWith(
        '/patterns/clusters/abc123/tier',
        { tier: 3 },
      )
    })
  })

  it('renders multiple clusters', async () => {
    mockGet.mockResolvedValue({ clusters: [CLUSTER_TASK, CLUSTER_VOCAB] })
    render(<PatternPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('pattern-cluster-abc123')).toBeDefined()
      expect(screen.getByTestId('pattern-cluster-def456')).toBeDefined()
    })
  })
})
