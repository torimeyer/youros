import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from './Dashboard'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

const mockDashboardData = {
  counts: { open: 5, closed: 12, p0: 1, p1: 3, p2: 1 },
  focus: [
    { title: 'Fix login flow', id: '001', priority: 'P0' },
  ],
  recent_tasks: [
    { id: '001', title: 'Fix login flow', priority: 'P0' },
  ],
  hay_count: 3,
  ostk_status: 'no daemon running',
}

const mockSummaryData = {
  bullets: [
    'You closed 4 tasks today. Nice work!',
    '5 tasks still open.',
    'Top priority: Fix login flow',
    'Agents today: 2 started, 1 finished.',
    '3 ideas saved and waiting for review.',
  ],
}

const mockCostData = {
  total_budget: 2.0,
  agent_count: 1,
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  )
}

describe('Dashboard Day Summary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/agents') return Promise.resolve({ active: [] })
      return Promise.resolve({})
    })
  })

  it('renders the Day Summary card', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
  })

  it('shows summary bullets from API', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('You closed 4 tasks today. Nice work!')).toBeInTheDocument()
      expect(screen.getByText('5 tasks still open.')).toBeInTheDocument()
      expect(screen.getByText('Top priority: Fix login flow')).toBeInTheDocument()
      expect(screen.getByText('Agents today: 2 started, 1 finished.')).toBeInTheDocument()
      expect(screen.getByText('3 ideas saved and waiting for review.')).toBeInTheDocument()
    })
  })

  it('shows empty state when summary returns no bullets', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve({ bullets: [] })
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/agents') return Promise.resolve({ active: [] })
      return Promise.resolve({})
    })

    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('No activity to summarize yet.')).toBeInTheDocument()
    })
  })

  it('has a Refresh button on the Day Summary card', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
    // There are multiple Refresh buttons (Today's Focus also has one)
    const refreshButtons = screen.getAllByText('Refresh')
    expect(refreshButtons.length).toBeGreaterThanOrEqual(2)
  })

  it('calls the summary API again when Refresh is clicked', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })

    const summaryCallsBefore = mockedApiGet.mock.calls.filter(
      (c) => c[0] === '/dashboard/summary'
    ).length

    // Find the Refresh button inside the Day Summary card
    const summaryCard = screen.getByText('Day Summary').closest('div[class*="bg-slate-900"]')!
    const refreshBtn = summaryCard.querySelector('button')!
    fireEvent.click(refreshBtn)

    await waitFor(() => {
      const summaryCallsAfter = mockedApiGet.mock.calls.filter(
        (c) => c[0] === '/dashboard/summary'
      ).length
      expect(summaryCallsAfter).toBeGreaterThan(summaryCallsBefore)
    })
  })

  it('fetches dashboard summary on initial load', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/dashboard/summary')
    })
  })
})
