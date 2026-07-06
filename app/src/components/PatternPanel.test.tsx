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
        '/api/patterns/clusters/abc123/tier',
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
        '/api/patterns/clusters/abc123/tier',
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
      expect(screen.getByTestId('approve-silent-abc123')).toBeDefined()
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
