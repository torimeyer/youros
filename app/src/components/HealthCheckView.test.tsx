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

const mockHealthResult = {
  tasks: [
    { id: '001', priority: 'P1', status: 'open', title: 'Fix bug', sphere: null, degree: 0, joints: [] },
    { id: '002', priority: 'P1', status: 'open', title: 'Add feature', sphere: null, degree: 2, joints: [
      { id: '003', title: 'Related task' },
      { id: '004', title: 'Another task' },
    ]},
  ],
  issues: [
    { type: 'no_description', severity: 'info', message: 'Task 001 has no description', task_ids: ['001'] },
    { type: 'isolated', severity: 'info', message: 'Task 001 is not linked to any other tasks', task_ids: ['001'] },
  ],
  summary: { total: 2, issues: 2, connected: 1, isolated: 1 },
}

const mockCleanResult = {
  tasks: [
    { id: '001', priority: 'P1', status: 'open', title: 'Good task', sphere: null, degree: 1, joints: [
      { id: '002', title: 'Linked task' }
    ]},
  ],
  issues: [],
  summary: { total: 1, issues: 0, connected: 1, isolated: 0 },
}

describe('HealthCheckView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the initial state with a run button', () => {
    render(<HealthCheckView />)

    expect(screen.getByText('Task Health Check')).toBeInTheDocument()
    expect(screen.getByText('Run Health Check')).toBeInTheDocument()
  })

  it('shows loading state while checking', async () => {
    mockedApiGet.mockReturnValue(new Promise(() => {})) // never resolves
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    expect(screen.getByText('Checking task quality...')).toBeInTheDocument()
  })

  it('displays summary after health check completes', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(screen.getByText('Tasks checked')).toBeInTheDocument()
    })

    // Check all summary cards exist
    expect(screen.getByText('Issues found')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('Isolated')).toBeInTheDocument()
  })

  it('shows issues list when problems are found', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(screen.getByText('Task 001 has no description')).toBeInTheDocument()
    })

    expect(screen.getByText('Task 001 is not linked to any other tasks')).toBeInTheDocument()
  })

  it('shows all-clear message when no issues found', async () => {
    mockedApiGet.mockResolvedValue(mockCleanResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(screen.getByText('All clear')).toBeInTheDocument()
    })
  })

  it('calls the correct API endpoint', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/health')
    })
  })

  it('shows error state when API fails', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(screen.getByText('Could not run the health check. Try again in a moment.')).toBeInTheDocument()
    })

    expect(screen.getByText('Try again')).toBeInTheDocument()
  })

  it('can re-run the health check', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      expect(screen.getByText('Issues to review')).toBeInTheDocument()
    })

    // Click the "Run again" button
    fireEvent.click(screen.getByText('Run again'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledTimes(2)
    })
  })

  it('shows issue type filter buttons', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      // Filter for all issues
      expect(screen.getByText(/All \(2\)/)).toBeInTheDocument()
    })
  })

  it('displays task IDs on issues', async () => {
    mockedApiGet.mockResolvedValue(mockHealthResult)
    render(<HealthCheckView />)

    fireEvent.click(screen.getByText('Run Health Check'))

    await waitFor(() => {
      // Task IDs should be shown with # prefix
      const taskIds = screen.getAllByText('#001')
      expect(taskIds.length).toBeGreaterThanOrEqual(1)
    })
  })
})
