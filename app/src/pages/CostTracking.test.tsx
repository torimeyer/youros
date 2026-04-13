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

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
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

const mockedApiGet = vi.mocked(api.get)

const mockCostData = {
  total_budget: 7.1,
  total_input_tokens: 125000,
  total_output_tokens: 42000,
  event_count: 3,
  agent_count: 3,
  by_model: [
    { model: 'claude-sonnet-4-5-20250929', count: 2, total_budget: 2.1, input_tokens: 50000, output_tokens: 18000 },
    { model: 'claude-opus-4-5-20250929', count: 1, total_budget: 5.0, input_tokens: 75000, output_tokens: 24000 },
  ],
  by_date: [
    { date: '2026-04-03', count: 1, total_budget: 5.0, input_tokens: 75000, output_tokens: 24000 },
    { date: '2026-04-04', count: 2, total_budget: 2.1, input_tokens: 50000, output_tokens: 18000 },
  ],
  by_type: [],
  agents: [
    { name: 'test-agent', model: 'claude-sonnet-4-5-20250929', budget: 0.1, timestamp: '2026-04-04T20:01:02Z' },
    { name: 'refactor-bot', model: 'claude-sonnet-4-5-20250929', budget: 2.0, timestamp: '2026-04-04T21:30:00Z' },
    { name: 'research-agent', model: 'claude-opus-4-5-20250929', budget: 5.0, timestamp: '2026-04-03T10:00:00Z' },
  ],
  period: 'all',
}

const mockEmptyCostData = {
  total_budget: 0,
  total_input_tokens: 0,
  total_output_tokens: 0,
  event_count: 0,
  agent_count: 0,
  by_model: [],
  by_date: [],
  by_type: [],
  agents: [],
  period: 'all',
}

const mockSavingsData = {
  available: true,
  savings_usd: 0.0781,
  cache_efficiency_pct: 61.1,
  compression_pct: 4.2,
  cost_without_ostk_usd: 0.1608,
  cost_with_ostk_usd: 0.0841,
  period: 'session',
}

const mockSavingsUnavailable = { available: false }

// Build a mock implementation of api.get that routes by path so tests can
// set different responses for /costs and /costs/savings without stomping
// on each other.
function routedApiGet(
  costs: unknown = mockCostData,
  savings: unknown = mockSavingsData,
) {
  return (path: string) => {
    if (path.startsWith('/costs/savings')) {
      return Promise.resolve(savings)
    }
    if (path.startsWith('/costs')) {
      return Promise.resolve(costs)
    }
    return Promise.resolve({})
  }
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
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
    mockedApiGet.mockImplementation(routedApiGet() as never)
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
      expect(screen.getByText(/3 agents spawned/)).toBeInTheDocument()
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
      expect(screen.getByText('Usage History')).toBeInTheDocument()
    })
    expect(screen.getByText('test-agent')).toBeInTheDocument()
    expect(screen.getByText('refactor-bot')).toBeInTheDocument()
    expect(screen.getByText('research-agent')).toBeInTheDocument()
  })

  it('shows spending over time chart', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Usage Over Time')).toBeInTheDocument()
    })
  })

  it('shows empty state when no agents', async () => {
    mockedApiGet.mockImplementation(routedApiGet(mockEmptyCostData) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('No spending data yet')).toBeInTheDocument()
    })
  })

  it('shows total AI calls from API', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText(/calls total/)).toBeInTheDocument()
    })
  })

  it('renders the myOS savings tile with the savings number when data is present', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('myos-savings-tile')).toBeInTheDocument()
    })
    // Savings tile headline
    expect(screen.getByText('myOS savings')).toBeInTheDocument()
    // Plain language labels (no finance jargon)
    expect(screen.getByText('myOS saved you this session')).toBeInTheDocument()
    expect(screen.getByText('Requests reused from memory')).toBeInTheDocument()
    expect(screen.getByText('Space saved on stored information')).toBeInTheDocument()
    // Numbers from the mock payload
    await waitFor(() => {
      expect(screen.getByText('$0.0781')).toBeInTheDocument()
    })
    expect(screen.getByText('61.1%')).toBeInTheDocument()
    expect(screen.getByText('4.2%')).toBeInTheDocument()
  })

  it('shows the empty state on the savings tile when the endpoint says unavailable', async () => {
    mockedApiGet.mockImplementation(
      routedApiGet(mockCostData, mockSavingsUnavailable) as never,
    )
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Savings data not available yet.')).toBeInTheDocument()
    })
    // Should not show any of the live labels
    expect(screen.queryByText('myOS saved you this session')).not.toBeInTheDocument()
    expect(screen.queryByText('Requests reused from memory')).not.toBeInTheDocument()
  })
})
