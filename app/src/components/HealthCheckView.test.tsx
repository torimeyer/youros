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
})
