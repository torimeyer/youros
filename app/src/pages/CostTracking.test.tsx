import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import CostTracking from './CostTracking'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

const mockCostData = {
  total_budget: 7.1,
  agent_count: 3,
  by_model: [
    { model: 'claude-sonnet-4-5-20250929', count: 2, total_budget: 2.1 },
    { model: 'claude-opus-4-5-20250929', count: 1, total_budget: 5.0 },
  ],
  by_date: [
    { date: '2026-04-03', count: 1, total_budget: 5.0 },
    { date: '2026-04-04', count: 2, total_budget: 2.1 },
  ],
  agents: [
    { name: 'test-agent', model: 'claude-sonnet-4-5-20250929', budget: 0.1, timestamp: '2026-04-04T20:01:02Z' },
    { name: 'refactor-bot', model: 'claude-sonnet-4-5-20250929', budget: 2.0, timestamp: '2026-04-04T21:30:00Z' },
    { name: 'research-agent', model: 'claude-opus-4-5-20250929', budget: 5.0, timestamp: '2026-04-03T10:00:00Z' },
  ],
  period: 'all',
}

const mockEmptyCostData = {
  total_budget: 0,
  agent_count: 0,
  by_model: [],
  by_date: [],
  agents: [],
  period: 'all',
}

function renderCostTracking() {
  return render(
    <MemoryRouter>
      <CostTracking />
    </MemoryRouter>
  )
}

describe('CostTracking page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'YourOS', darkMode: true })
    mockedApiGet.mockResolvedValue(mockCostData)
  })

  it('renders the page title', async () => {
    renderCostTracking()
    expect(screen.getByText('AI Spending')).toBeInTheDocument()
  })

  it('shows total budget from API', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('$7.10')).toBeInTheDocument()
    })
  })

  it('shows agent count from API', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    })
  })

  it('shows time filter buttons', () => {
    renderCostTracking()
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('This Week')).toBeInTheDocument()
    expect(screen.getByText('This Month')).toBeInTheDocument()
    expect(screen.getByText('All Time')).toBeInTheDocument()
  })

  it('switches period when clicking filter button', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('$7.10')).toBeInTheDocument()
    })

    const todayBtn = screen.getByText('Today')
    fireEvent.click(todayBtn)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs?period=today')
    })
  })

  it('shows model breakdown section', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('By Model')).toBeInTheDocument()
    })
    // Model names should be shortened (appears in breakdown + agent table)
    expect(screen.getAllByText('Sonnet 4.5').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Opus 4.5').length).toBeGreaterThanOrEqual(1)
  })

  it('shows agent history table', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Agent History')).toBeInTheDocument()
    })
    expect(screen.getByText('test-agent')).toBeInTheDocument()
    expect(screen.getByText('refactor-bot')).toBeInTheDocument()
    expect(screen.getByText('research-agent')).toBeInTheDocument()
  })

  it('shows spending over time chart', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Budget Allocation Over Time')).toBeInTheDocument()
    })
  })

  it('shows empty state when no agents', async () => {
    mockedApiGet.mockResolvedValue(mockEmptyCostData)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('No agent spending data yet')).toBeInTheDocument()
    })
  })

  it('shows average budget per agent', async () => {
    renderCostTracking()
    await waitFor(() => {
      // 7.10 / 3 = 2.37
      expect(screen.getByText('$2.37')).toBeInTheDocument()
    })
  })
})
