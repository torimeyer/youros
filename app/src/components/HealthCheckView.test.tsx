import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import HealthCheckView from './HealthCheckView'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

// The new HealthCheckView calls BOTH /tasks/health AND /tasks/duplicates
// in parallel, then filters out the noisy "isolated" issue type and
// renders duplicate candidates as a separate section.
const mockHealthResult = {
  tasks: [
    { id: '001', priority: 'P1', status: 'open', title: 'Fix bug', sphere: null, degree: 0, joints: [] },
    { id: '002', priority: 'P1', status: 'open', title: 'Add feature', sphere: null, degree: 1, joints: [
      { id: '003', title: 'Related task' },
    ]},
  ],
  issues: [
    { type: 'no_description', severity: 'info', message: 'Task 001 has no description', task_ids: ['001'] },
  ],
  summary: { total: 2, issues: 1, connected: 1, isolated: 1 },
}

const mockDuplicatesResult = {
  duplicates: [
    {
      task_a: { id: '001', title: 'Fix bug', priority: 'P1' },
      task_b: { id: '099', title: 'Fix the bug', priority: 'P1' },
      similarity: 0.92,
    },
  ],
}

function mockBothEndpoints() {
  mockedApiGet.mockImplementation((url: string) => {
    if (url.includes('/tasks/duplicates')) return Promise.resolve(mockDuplicatesResult)
    if (url.includes('/tasks/health')) return Promise.resolve(mockHealthResult)
    return Promise.resolve({})
  })
}

describe('HealthCheckView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the initial state with a run button', () => {
    render(<HealthCheckView />)
    expect(screen.getByText(/Run Health Check/i)).toBeInTheDocument()
  })

  it('calls both /tasks/health and /tasks/duplicates when the user runs a check', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/health')
    })
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/duplicates')
    })
  })

  it('renders a missing description issue', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByText(/Task 001 has no description/i)).toBeInTheDocument()
    })
  })

  it('renders a duplicate candidate card', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByText(/Fix bug/)).toBeInTheDocument()
      expect(screen.getByText(/Fix the bug/)).toBeInTheDocument()
    })
  })

  it('gracefully handles an empty result', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.includes('/tasks/duplicates')) return Promise.resolve({ duplicates: [] })
      if (url.includes('/tasks/health')) {
        return Promise.resolve({
          tasks: [],
          issues: [],
          summary: { total: 0, issues: 0, connected: 0, isolated: 0 },
        })
      }
      return Promise.resolve({})
    })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/health')
    })
  })

  it('renders Resolve and Resolve all buttons when duplicates exist', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByText(/Fix the bug/)).toBeInTheDocument()
    })
    expect(screen.getByTestId('resolve-pair-0')).toBeInTheDocument()
    expect(screen.getByTestId('resolve-all-button')).toBeInTheDocument()
  })

  it('calls POST /tasks/duplicates/resolve when Resolve is clicked', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ resolved: 1, closed: '099', kept: '001' })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('resolve-pair-0')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('resolve-pair-0'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/tasks/duplicates/resolve',
        { keep_id: '001', discard_id: '099' }
      )
    })
  })

  it('shows success feedback and removes pair after resolving', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ resolved: 1, closed: '099', kept: '001' })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('resolve-pair-0')).toBeInTheDocument()
    })

    // Update mock for the refresh call
    mockedApiGet.mockImplementation((url: string) => {
      if (url.includes('/tasks/duplicates')) return Promise.resolve({ duplicates: [] })
      if (url.includes('/tasks/health')) return Promise.resolve(mockHealthResult)
      return Promise.resolve({})
    })

    fireEvent.click(screen.getByTestId('resolve-pair-0'))
    await waitFor(() => {
      expect(screen.queryByText(/Fix the bug/)).not.toBeInTheDocument()
    })
    expect(screen.getByText(/Closed #099 as duplicate/i)).toBeInTheDocument()
  })

  it('calls POST /tasks/duplicates/resolve-all when Resolve all is clicked', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ resolved: 1, closed: ['099'] })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('resolve-all-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('resolve-all-button'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/duplicates/resolve-all', {})
    })
  })

  it('clears all pairs and shows count after resolve-all succeeds', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ resolved: 1, closed: ['099'] })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('resolve-all-button')).toBeInTheDocument()
    })

    // Update mock for the refresh call
    mockedApiGet.mockImplementation((url: string) => {
      if (url.includes('/tasks/duplicates')) return Promise.resolve({ duplicates: [] })
      if (url.includes('/tasks/health')) return Promise.resolve(mockHealthResult)
      return Promise.resolve({})
    })

    fireEvent.click(screen.getByTestId('resolve-all-button'))
    await waitFor(() => {
      expect(screen.queryByText(/Fix the bug/)).not.toBeInTheDocument()
    })
    expect(screen.getByText(/Closed 1 duplicate task/i)).toBeInTheDocument()
  })

  it('shows error feedback when resolve fails', async () => {
    mockBothEndpoints()
    mockedApiPost.mockRejectedValueOnce(new Error('Task t-999 not found'))
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('resolve-pair-0')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('resolve-pair-0'))
    await waitFor(() => {
      expect(screen.getByText(/Task t-999 not found/i)).toBeInTheDocument()
    })
  })

  // --- per-issue Fix button ---

  it('renders a Fix button for no_description issues', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-button-no_description-001')).toBeInTheDocument()
    })
  })

  it('calls POST /tasks/health/autofix/no_description when Fix is clicked', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ fixed: true, details: 'Added description: A short summary.' })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-button-no_description-001')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-button-no_description-001'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/tasks/health/autofix/no_description',
        { task_id: '001' }
      )
    })
  })

  it('shows success check after Fix succeeds', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ fixed: true, details: 'Added description: Done.' })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-button-no_description-001')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-button-no_description-001'))
    await waitFor(() => {
      expect(screen.getByTestId('fix-success-no_description-001')).toBeInTheDocument()
    })
  })

  it('shows error banner when Fix fails', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ fixed: false, reason: 'No API key configured.' })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-button-no_description-001')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-button-no_description-001'))
    await waitFor(() => {
      expect(screen.getByTestId('fix-error-no_description-001')).toBeInTheDocument()
    })
  })

  // --- Fix all button and confirm modal ---

  it('renders a Fix all button when fixable issues exist', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-button')).toBeInTheDocument()
    })
  })

  it('opens the confirm modal when Fix all is clicked', async () => {
    mockBothEndpoints()
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-all-button'))
    expect(screen.getByTestId('fix-all-confirm-modal')).toBeInTheDocument()
  })

  it('calls POST /tasks/health/autofix-all after confirming', async () => {
    mockBothEndpoints()
    // First post call is fix-all, then health and duplicates are re-fetched
    mockedApiPost.mockResolvedValueOnce({ fixed: 1, skipped: 0, details: [] })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.includes('/tasks/duplicates')) return Promise.resolve(mockDuplicatesResult)
      if (url.includes('/tasks/health')) return Promise.resolve(mockHealthResult)
      return Promise.resolve({})
    })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-all-button'))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-confirm-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-all-confirm-button'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/health/autofix-all', {})
    })
  })

  it('shows fix-all success message after autofix-all completes', async () => {
    mockBothEndpoints()
    mockedApiPost.mockResolvedValueOnce({ fixed: 2, skipped: 0, details: [] })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.includes('/tasks/duplicates')) return Promise.resolve(mockDuplicatesResult)
      if (url.includes('/tasks/health')) return Promise.resolve(mockHealthResult)
      return Promise.resolve({})
    })
    render(<HealthCheckView />)
    fireEvent.click(screen.getByText(/Run Health Check/i))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-all-button'))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-confirm-button')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('fix-all-confirm-button'))
    await waitFor(() => {
      expect(screen.getByTestId('fix-all-message')).toBeInTheDocument()
    })
    expect(screen.getByText(/Fixed 2 issues/i)).toBeInTheDocument()
  })
})
