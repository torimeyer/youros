import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
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
    { name: 'test-agent', event: 'agent.spawned', model: 'claude-sonnet-4-5-20250929', budget: 0.1, input_tokens: 50000, output_tokens: 18000, timestamp: '2026-04-04T20:01:02Z', message_count: 1 },
    { name: 'refactor-bot', event: 'agent.spawned', model: 'claude-sonnet-4-5-20250929', budget: 2.0, input_tokens: 75000, output_tokens: 24000, timestamp: '2026-04-04T21:30:00Z', message_count: 1 },
    { name: 'research-agent', event: 'agent.spawned', model: 'claude-opus-4-5-20250929', budget: 5.0, input_tokens: 0, output_tokens: 0, timestamp: '2026-04-03T10:00:00Z', message_count: 1 },
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
  subscription_savings_usd: 234.31,
  subscription_input_tokens: 22321293,
  subscription_output_tokens: 11156249,
  conversation_cache_tokens: 204000,
  squash_tokens_saved: 0,
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

const mockWeekData = {
  ...mockCostData,
  total_budget: 3.5,
  event_count: 2,
  agent_count: 2,
  agents: [
    { name: 'week-agent', event: 'agent.spawned', model: 'claude-sonnet-4-5-20250929', budget: 3.5, input_tokens: 80000, output_tokens: 22000, timestamp: '2026-04-12T10:00:00Z', message_count: 1 },
  ],
  period: 'week',
}

describe('CostTracking page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear localStorage cache between tests to avoid cross-test pollution.
    localStorage.clear()
    useAppStore.setState({ chatOpen: false, osName: 'yourOS', darkMode: true, showBudgetCaps: false })
    mockedApiGet.mockImplementation(routedApiGet() as never)
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('renders the spending tab', async () => {
    renderCostTracking()
    expect(screen.getByTestId('tab-spending')).toBeInTheDocument()
  })

  it('page title bar shows "Usage"', async () => {
    renderCostTracking()
    // h1 heading
    expect(screen.getByRole('heading', { name: 'Usage', level: 1 })).toBeInTheDocument()
  })

  it('shows time filter buttons', () => {
    renderCostTracking()
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('This Week')).toBeInTheDocument()
    expect(screen.getByText('This Month')).toBeInTheDocument()
  })

  it('switches period when clicking filter button', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })

    const todayBtn = screen.getByText('Today')
    fireEvent.click(todayBtn)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs?period=today')
    })
  })

  it('shows agent count from API in summary card', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    // Summary card shows event count and label
    expect(screen.getByText(/total calls/)).toBeInTheDocument()
    // "Agents spawned" label exists in the summary card
    expect(screen.getByText('Agents spawned')).toBeInTheDocument()
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

  it('shows error card with retry button when fetch fails', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('cost-error')).toBeInTheDocument()
    })
    expect(screen.getByText("Couldn't load usage data")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry loading usage data/i })).toBeInTheDocument()
  })

  // ostk-only savings card (Anthropic cache is intentionally not surfaced)
  it('renders the ostk savings card only (no anthropic cache card)', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('anthropic-cache-card')).not.toBeInTheDocument()
  })

  it('ostk card does not contain Anthropic cache values', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      {
        ...mockSavingsData,
        conversation_cache_tokens: 458664,
        cache_efficiency_pct: 61.1,
        compression_pct: 14.08,
      },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    const ostkCard = screen.getByTestId('ostk-savings-card')
    // Raw Anthropic token count must never leak to the ostk card
    expect(ostkCard).not.toHaveTextContent('458,664')
    // ostk-native compression does belong here (toFixed(1) rounds 14.08 to 14.1)
    expect(ostkCard).toHaveTextContent('14.1%')
  })

  it('ostk card does not show n/a placeholder tiles for needle-recall and hay-hit', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    const ostkCard = screen.getByTestId('ostk-savings-card')
    // n/a tiles must not render. they hurt the demo when data is unavailable
    expect(ostkCard.querySelector('[data-testid="stat-needle-recall"]')).not.toBeInTheDocument()
    expect(ostkCard.querySelector('[data-testid="stat-hay-hit"]')).not.toBeInTheDocument()
    const placeholders = ostkCard.querySelectorAll('[aria-label="not yet available"]')
    expect(placeholders).toHaveLength(0)
  })

  it('renders exactly one savings tile with all kept stats', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    // Heading present
    expect(screen.getByText('ostk savings')).toBeInTheDocument()
    // Kept stats
    expect(screen.getByText('Context compression')).toBeInTheDocument()
    // Old juvenile label must be gone
    expect(screen.queryByText('How much smaller your context is')).not.toBeInTheDocument()
    // Removed stats must not appear
    expect(screen.queryByText('yourOS savings')).not.toBeInTheDocument()
    expect(screen.queryByText('ostk token savings')).not.toBeInTheDocument()
    expect(screen.queryByText('yourOS saved you this session')).not.toBeInTheDocument()
    expect(screen.queryByText('Space saved on stored information')).not.toBeInTheDocument()
    expect(screen.queryByText('Anthropic prompt cache')).not.toBeInTheDocument()
    expect(screen.queryByText('Tokens read from cache')).not.toBeInTheDocument()
    // Dollar amount must not appear
    expect(screen.queryByText(/\$0\.0781/)).not.toBeInTheDocument()
  })

  it('shows explain popover trigger on the compression stat label', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    const trigger = screen.getByTestId('explain-trigger-context_compression')
    expect(trigger).toBeInTheDocument()
    expect(trigger.getAttribute('aria-label')).toMatch(/Context compression/)
  })

  it('shows savings unavailable message when endpoint says unavailable', async () => {
    mockedApiGet.mockImplementation(
      routedApiGet(mockCostData, { available: false }) as never,
    )
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Savings data not available yet.')).toBeInTheDocument()
    })
    expect(screen.queryByText('Requests reused from memory')).not.toBeInTheDocument()
  })

  it('shows the empty state when the endpoint says unavailable', async () => {
    mockedApiGet.mockImplementation(
      routedApiGet(mockCostData, mockSavingsUnavailable) as never,
    )
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('Savings data not available yet.')).toBeInTheDocument()
    })
    // Should not show any of the live labels
    expect(screen.queryByText('yourOS saved you this session')).not.toBeInTheDocument()
    expect(screen.queryByText('Requests reused from memory')).not.toBeInTheDocument()
  })

  it('does not show stale data when a fast filter switch resolves after a slow one', async () => {
    // Simulate: user clicks "Week" (slow, resolves 2nd) then "Today" (fast, resolves 1st).
    // The final state must reflect "Today" data, not the stale "Week" response.
    let resolveWeek!: (v: unknown) => void
    let resolveToday!: (v: unknown) => void
    const weekPromise = new Promise((res) => { resolveWeek = res })
    const todayData = { ...mockCostData, event_count: 1, agent_count: 1, period: 'today' }

    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.includes('period=week')) return weekPromise
      if (path.includes('period=today')) return new Promise((res) => { resolveToday = res })
      return Promise.resolve(mockCostData)
    })

    localStorage.setItem('myos_cost_period', 'month')
    renderCostTracking()

    // Initial "month" load -- wait for summary card
    await waitFor(() => expect(screen.getByTestId('summary-card')).toBeInTheDocument())

    // Click Week (will be slow)
    fireEvent.click(screen.getByText('This Week'))

    // Immediately click Today (will resolve first)
    fireEvent.click(screen.getByText('Today'))

    // Resolve Today first
    act(() => { resolveToday(todayData) })

    await waitFor(() => {
      // 1 agent from today data
      expect(screen.getByText('1')).toBeInTheDocument()
    })

    // Now resolve the stale Week response -- it must NOT overwrite Today data
    act(() => { resolveWeek(mockWeekData) })

    // Give React a tick to flush any state updates
    await new Promise((r) => setTimeout(r, 50))

    // Today shows 1 call total, week would have shown 2 -- verify week didn't win
    // Verify week data (2 calls) did not overwrite today (1 call)
    expect(screen.queryByText(/across 2 total calls/)).not.toBeInTheDocument()
  })

  it('shows correct data after multiple rapid filter changes (last one wins)', async () => {
    let resolveWeek: ((v: unknown) => void) | undefined
    let resolveMonth: ((v: unknown) => void) | undefined
    const weekData = { ...mockCostData, event_count: 2, agent_count: 2, period: 'week' }
    const monthData = { ...mockCostData, event_count: 5, agent_count: 4, period: 'month' }

    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.includes('period=today')) return Promise.resolve({ ...mockCostData, event_count: 1, agent_count: 1, period: 'today' })
      if (path.includes('period=week'))
        return new Promise((res) => { resolveWeek = res })
      if (path.includes('period=month'))
        return new Promise((res) => { resolveMonth = res })
      return Promise.resolve(mockCostData)
    })

    // Start on 'today' so the initial load resolves immediately via the mocked handler.
    localStorage.setItem('myos_cost_period', 'today')
    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('summary-card')).toBeInTheDocument())

    // Click Week -- starts a slow request
    fireEvent.click(screen.getByText('This Week'))
    await waitFor(() => expect(resolveWeek).toBeDefined())

    // Click Month -- starts another slow request; this is the FINAL selection
    fireEvent.click(screen.getByText('This Month'))
    await waitFor(() => expect(resolveMonth).toBeDefined())

    // Resolve Month first (it has the highest fetchId, it wins)
    act(() => { resolveMonth!(monthData) })
    // Month has agent_count 4, so "4" should appear in the summary
    await waitFor(() => expect(screen.getByText('4')).toBeInTheDocument())

    // Resolve the stale Week response -- it must NOT overwrite Month data
    act(() => { resolveWeek!(weekData) })
    await new Promise((r) => setTimeout(r, 50))

    // Month: 4 agents, week: 2 agents. After resolving week, we still see month data.
    // Verify week data (2 calls) did not overwrite today (1 call)
    expect(screen.queryByText(/across 2 total calls/)).not.toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('paints primary data from localStorage on mount without waiting for API', () => {
    // Pre-seed localStorage to simulate a returning user.
    localStorage.setItem('myos_cost_data_cache', JSON.stringify(mockCostData))

    // Mock API to never resolve so we can prove the cached paint happened synchronously.
    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    renderCostTracking()

    // Summary card must be visible immediately (no await) -- seeded from localStorage.
    expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    expect(screen.queryByTestId('cost-loading')).not.toBeInTheDocument()
  })

  it('paints savings from localStorage on mount without waiting for API', () => {
    // Pre-seed both caches. The default period is 'week', so seed the per-period key.
    localStorage.setItem('myos_cost_data_cache', JSON.stringify(mockCostData))
    localStorage.setItem('myos_savings_data_cache_week', JSON.stringify(mockSavingsData))

    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    renderCostTracking()

    // ostk savings card must paint immediately from localStorage -- no "Loading" text
    expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    expect(screen.queryByTestId('anthropic-cache-card')).not.toBeInTheDocument()
    expect(screen.queryByText('Loading savings data...')).not.toBeInTheDocument()
  })

  // Budget caps: hidden by default
  it('does not render budget caps stat or column when showBudgetCaps=false (default)', async () => {
    useAppStore.setState({ showBudgetCaps: false })
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('budget-caps-stat')).not.toBeInTheDocument()
    expect(screen.queryByTestId('budget-col-header')).not.toBeInTheDocument()
  })

  // Budget caps: visible when toggled on
  it('shows budget caps stat and column when showBudgetCaps=true', async () => {
    useAppStore.setState({ showBudgetCaps: true })
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('budget-caps-stat')).toBeInTheDocument()
    expect(screen.getByTestId('budget-col-header')).toBeInTheDocument()
  })

  // Prompt Efficiency: broken 0.0:1 must not appear
  it('does not show broken 0.0:1 prompt efficiency ratio', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    expect(screen.queryByText('0.0:1')).not.toBeInTheDocument()
    expect(screen.queryByText('Prompt Efficiency')).not.toBeInTheDocument()
  })

  // Token split tiles
  it('renders separate input and output token tiles with correct values', async () => {
    // Provide a payload where cache reads dominate to prove the input tile
    // shows the combined total rather than just the raw input_tokens field.
    const cachedData = {
      ...mockCostData,
      total_input_tokens: 25_000,
      total_cache_creation_tokens: 10_000,
      total_cache_read_tokens: 90_000,
      total_input_tokens_with_cache: 125_000,
      total_output_tokens: 42_000,
    }
    mockedApiGet.mockImplementation(routedApiGet(cachedData) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
    expect(screen.getByTestId('output-tokens-tile')).toBeInTheDocument()
    // Input tile shows the combined total (25K + 10K + 90K = 125K), not 25K.
    expect(screen.getByText('125K')).toBeInTheDocument()
    expect(screen.getByText('42K')).toBeInTheDocument()
    // Labels
    expect(screen.getByText('Input tokens used')).toBeInTheDocument()
    expect(screen.getByText('Output tokens used')).toBeInTheDocument()
    // Old combined tile must be gone
    expect(screen.queryByText('Total tokens used')).not.toBeInTheDocument()
  })

  // When the backend does not yet send total_input_tokens_with_cache, the
  // tile falls back to summing raw input plus cache creation plus cache reads.
  it('falls back to summing raw input plus cache fields when combined total is absent', async () => {
    const legacyData = {
      ...mockCostData,
      total_input_tokens: 10_000,
      total_cache_creation_tokens: 5_000,
      total_cache_read_tokens: 60_000,
      // total_input_tokens_with_cache intentionally omitted
      total_output_tokens: 20_000,
    }
    mockedApiGet.mockImplementation(routedApiGet(legacyData) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
    })
    // 10K + 5K + 60K = 75K
    expect(screen.getByText('75K')).toBeInTheDocument()
  })

  // Token tile secondary lines show precise "% of total" when tokens are non-zero
  it('token tiles show "% of total" with 2-decimal precision when value is under 1%', async () => {
    // input=496M, output=1.3M  => output pct ≈ 0.26%, input pct ≈ 99.74%
    const precisionData = {
      ...mockCostData,
      total_input_tokens: 496_000_000,
      total_cache_creation_tokens: 0,
      total_cache_read_tokens: 0,
      total_input_tokens_with_cache: 496_000_000,
      total_output_tokens: 1_300_000,
    }
    mockedApiGet.mockImplementation(routedApiGet(precisionData) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
      expect(screen.getByTestId('output-tokens-tile')).toBeInTheDocument()
    })
    // output is 1.3M / 497.3M ≈ 0.26%, must use toFixed(2)
    const outputTile = screen.getByTestId('output-tokens-tile')
    expect(outputTile).toHaveTextContent(/0\.\d\d% of total/)
    // input is ~99.74%, must use toFixed(1)
    const inputTile = screen.getByTestId('input-tokens-tile')
    expect(inputTile).toHaveTextContent(/99\.\d% of total/)
  })

  it('token tiles show "% of total" with 1-decimal precision when value is 1% or more', async () => {
    // Use mockCostData: input=125K, output=42K => ~74.8% input, ~25.2% output
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
      expect(screen.getByTestId('output-tokens-tile')).toBeInTheDocument()
    })
    const inputTile = screen.getByTestId('input-tokens-tile')
    const outputTile = screen.getByTestId('output-tokens-tile')
    expect(inputTile).toHaveTextContent(/% of total/)
    expect(outputTile).toHaveTextContent(/% of total/)
    // Verify 1-decimal format (not 2-decimal) for values >= 1%
    // 125K/(125K+42K)*100 ≈ 74.9%, should appear as e.g. "74.9% of total"
    expect(inputTile.textContent).toMatch(/\d+\.\d% of total/)
  })

  it('token tiles fall back to plain text when total tokens is zero', async () => {
    mockedApiGet.mockImplementation(routedApiGet(mockEmptyCostData) as never)
    renderCostTracking()
    // Empty state renders instead of tiles when event_count is 0
    await waitFor(() => {
      expect(screen.getByText('No spending data yet')).toBeInTheDocument()
    })
    expect(screen.queryByText(/% of total/)).not.toBeInTheDocument()
  })

  // Explain popover trigger must be accessible from the Input tokens tile.
  it('input tokens tile has an explain popover trigger', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
    })
    const tile = screen.getByTestId('input-tokens-tile')
    const trigger = tile.querySelector('[data-testid="explain-trigger-input_tokens"]')
    expect(trigger).not.toBeNull()
    const label = (trigger?.getAttribute('aria-label') ?? '').toLowerCase()
    expect(label).toContain('input tokens used')
    // Jargon must not appear in the user-facing trigger label.
    expect(label).not.toContain('cache read')
  })

  // Plain-language stat headings in savings card
  it('uses plain-language headings in the savings stat rows', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })
    // Compression tile renders with real data (mockSavingsData has compression_pct: 4.2)
    expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    expect(screen.getByText('Context compression')).toBeInTheDocument()
    // Old juvenile label must be absent
    expect(screen.queryByText('How much smaller your context is')).not.toBeInTheDocument()
    // needle-recall and hay-hit tiles are hidden until real data is available
    expect(screen.queryByTestId('stat-needle-recall')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stat-hay-hit')).not.toBeInTheDocument()
    expect(screen.queryByText('Needle recall rate')).not.toBeInTheDocument()
    expect(screen.queryByText('Context reused from memory')).not.toBeInTheDocument()
    // Old jargon headings must be gone
    expect(screen.queryByText('Cache hit rate')).not.toBeInTheDocument()
    expect(screen.queryByText('Compression')).not.toBeInTheDocument()
    expect(screen.queryByText('Requests reused from memory')).not.toBeInTheDocument()
  })

  // Suggestions section: low cache rate warning
  it('renders Suggestions section with low-cache tip when cache hit rate < 40%', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, cache_efficiency_pct: 25 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    expect(screen.getByText(/Low cache reuse/)).toBeInTheDocument()
  })

  it('low-cache suggestion does not contain "preamble" or "system prompt"', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, cache_efficiency_pct: 25 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    const section = screen.getByTestId('suggestions-section')
    expect(section.textContent).not.toMatch(/preamble/i)
    expect(section.textContent).not.toMatch(/system prompt/i)
  })

  it('low-cache suggestion uses plain language about Settings', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, cache_efficiency_pct: 25 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    expect(screen.getByText(/Save your standing instructions in Settings so it remembers between messages/)).toBeInTheDocument()
  })

  // Context reuse rate label: low rate must show actionable link, never vague "Room to improve"
  it('context reuse stat shows actionable link to Settings when rate is below 50%', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      { ...mockCostData, total_input_tokens: 500_000 },
      { ...mockSavingsData, cache_efficiency_pct: 30 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('30.0%')).toBeInTheDocument()
    })
    const link = screen.getByRole('link', { name: /Save standing instructions to raise this/i })
    expect(link).toBeInTheDocument()
    // Hash points at the suggest flow so one click opens the auto-draft
    // checklist instead of just scrolling to the textarea.
    expect(link).toHaveAttribute('href', '/settings#standing-instructions-suggest')
    // Must not fall back to the dead label
    expect(screen.queryByText('Room to improve')).not.toBeInTheDocument()
  })

  it('context reuse stat never shows "Room to improve" text', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      { ...mockCostData, total_input_tokens: 500_000 },
      { ...mockSavingsData, cache_efficiency_pct: 10 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('10.0%')).toBeInTheDocument()
    })
    expect(screen.queryByText('Room to improve')).not.toBeInTheDocument()
  })

  it('context reuse stat shows "Good reuse" when rate is 50% or above', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      { ...mockCostData, total_input_tokens: 500_000 },
      { ...mockSavingsData, cache_efficiency_pct: 65 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByText('65.0%')).toBeInTheDocument()
    })
    expect(screen.getByText('Good reuse')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Save standing instructions/i })).not.toBeInTheDocument()
  })

  it('renders Suggestions section with non-zero usage and no stale compression copy', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      { ...mockCostData, total_input_tokens: 800_000, total_output_tokens: 400_000 },
      { ...mockSavingsData, cache_efficiency_pct: 60 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    // Old compression toggle copy must not appear.
    expect(screen.queryByText(/Compression is on by default/)).not.toBeInTheDocument()
    expect(screen.queryByText(/You can turn it off in Settings if you prefer/)).not.toBeInTheDocument()
    // Old "Daily habits" copy must be gone
    expect(screen.queryByText(/Daily habits in Settings/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Consider enabling compression/)).not.toBeInTheDocument()
  })

  it('renders Suggestions section with no-usage tip when tokens are 0', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockEmptyCostData,
      { ...mockSavingsData, cache_efficiency_pct: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    expect(screen.getByText(/No usage yet/)).toBeInTheDocument()
  })

  // Top-row consolidation: single summary card
  it('shows exactly one summary card instead of multiple stat cards', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    const summaryCards = screen.getAllByTestId('summary-card')
    expect(summaryCards).toHaveLength(1)
    // Old multi-card labels must not exist as standalone cards
    expect(screen.queryByText('Budget Caps')).not.toBeInTheDocument()
    expect(screen.queryByText('Cap per Agent')).not.toBeInTheDocument()
    expect(screen.queryByText('Cap per Call')).not.toBeInTheDocument()
    expect(screen.queryByText('Prompt Efficiency')).not.toBeInTheDocument()
  })

  // --- TimeFilter integration ---

  it('renders shared TimeFilter pill row', () => {
    renderCostTracking()
    expect(screen.getByTestId('time-filter-today')).toBeInTheDocument()
    expect(screen.getByTestId('time-filter-week')).toBeInTheDocument()
    expect(screen.getByTestId('time-filter-month')).toBeInTheDocument()
  })

  it('clicking "This Week" pill fetches with period=week', async () => {
    renderCostTracking()
    fireEvent.click(screen.getByTestId('time-filter-week'))
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs?period=week')
    })
  })

  it('restores period=all from localStorage and fetches with period=all', async () => {
    localStorage.setItem('myos_cost_period', 'all')
    renderCostTracking()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs?period=all')
    })
  })

  it('switching from This Month to This Week triggers a new savings fetch with ?period=week', async () => {
    // Start on "month" so the initial mount fetches period=month
    localStorage.setItem('myos_cost_period', 'month')
    renderCostTracking()

    // Wait for the initial savings fetch with period=month
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs/savings?period=month')
    })

    vi.clearAllMocks()
    mockedApiGet.mockImplementation(routedApiGet() as never)

    // Click This Week
    fireEvent.click(screen.getByText('This Week'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs/savings?period=week')
    })
  })

  it('savings fetch uses the current period query param for each pill', async () => {
    localStorage.setItem('myos_cost_period', 'month')
    renderCostTracking()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs/savings?period=month')
    })

    vi.clearAllMocks()
    mockedApiGet.mockImplementation(routedApiGet() as never)
    fireEvent.click(screen.getByText('Today'))
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs/savings?period=today')
    })

    vi.clearAllMocks()
    mockedApiGet.mockImplementation(routedApiGet() as never)
    fireEvent.click(screen.getByText('This Month'))
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs/savings?period=month')
    })
  })
  // Change 1: AI summary must never mention dollars
  it('AI summary uses token count, not dollar amount, when savings data has savings_usd', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, savings_usd: 1.23, subscription_input_tokens: 57200 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('tab-spending')).toBeInTheDocument()
    })
    // Wait for API data to load
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    // Dollar sign must not appear in the AI summary
    const summaryEl = document.querySelector('.border-blue-500')
    if (summaryEl) {
      expect(summaryEl.textContent).not.toMatch(/\$\d+\.\d+/)
    }
    // No dollar amounts anywhere except budget caps (which are off by default)
    expect(screen.queryByText(/ostk saved \$\d/)).not.toBeInTheDocument()
  })

  // Change 2: "Daily habits" string must never appear in source
  it('suggestion copy does not reference "Daily habits in Settings"', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      { ...mockCostData, total_input_tokens: 800_000 },
      { ...mockSavingsData, cache_efficiency_pct: 60 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('suggestions-section')).toBeInTheDocument()
    })
    expect(screen.queryByText(/Daily habits/)).not.toBeInTheDocument()
    expect(screen.queryByText(/You can turn it off in Settings if you prefer/)).not.toBeInTheDocument()
  })

  // Change 3a: compression=0 hides the compression tile, shows empty state
  it('shows empty-state copy and hides stat tiles when compression_pct is 0', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 0, available: true, conversation_cache_tokens: 0, squash_tokens_saved: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('savings-no-data')).toBeInTheDocument()
    })
    expect(screen.getByText('Savings will appear here as ostk compresses your context.')).toBeInTheDocument()
    // Stat tiles must not render when all stats are zero
    expect(screen.queryByTestId('stat-compression')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stat-needle-recall')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stat-hay-hit')).not.toBeInTheDocument()
  })

  // Change 3b: non-zero compression shows tile, hides n/a tiles
  it('shows compression tile but not n/a tiles when compression is non-zero', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 20.64 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    })
    expect(screen.getByTestId('stat-compression')).toHaveTextContent('20.6%')
    // Empty-state must NOT appear
    expect(screen.queryByTestId('savings-no-data')).not.toBeInTheDocument()
    // n/a tiles must not appear
    expect(screen.queryByTestId('stat-needle-recall')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stat-hay-hit')).not.toBeInTheDocument()
    expect(screen.queryByText('n/a')).not.toBeInTheDocument()
  })

  // --- New companion stats ---

  it('renders 2 stat tiles side by side when compression and tokens-saved are both non-zero', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 5.2, conversation_cache_tokens: 204000, squash_tokens_saved: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
      expect(screen.getByTestId('stat-tokens-saved')).toBeInTheDocument()
    })
    // Both tiles visible
    expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    expect(screen.getByTestId('stat-tokens-saved')).toBeInTheDocument()
    // Time-saved also shows (204000 tokens / 10000 = 20.4s >= 1s)
    expect(screen.getByTestId('stat-time-saved')).toBeInTheDocument()
  })

  it('renders 3 stat tiles when compression, tokens-saved, and time-saved are all non-zero', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 8.0, conversation_cache_tokens: 500000, squash_tokens_saved: 50000 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    })
    expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    expect(screen.getByTestId('stat-tokens-saved')).toBeInTheDocument()
    expect(screen.getByTestId('stat-time-saved')).toBeInTheDocument()
  })

  it('shows single tile with single-column grid when only compression is non-zero', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 3.1, conversation_cache_tokens: 0, squash_tokens_saved: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-compression')).toBeInTheDocument()
    })
    // Only compression tile renders
    expect(screen.queryByTestId('stat-tokens-saved')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stat-time-saved')).not.toBeInTheDocument()
    // The grid wrapper uses single-column class when only one tile exists
    const compressionTile = screen.getByTestId('stat-compression')
    const grid = compressionTile.parentElement
    expect(grid?.className).toContain('grid-cols-1')
    expect(grid?.className).not.toContain('sm:grid-cols-2')
  })

  it('formatTokens: 1500 formats as "1.5K"', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 4.2, conversation_cache_tokens: 1500, squash_tokens_saved: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-tokens-saved')).toBeInTheDocument()
    })
    expect(screen.getByTestId('stat-tokens-saved')).toHaveTextContent('1.5K')
  })

  it('formatTokens: 1200000 formats as "1.2M"', async () => {
    mockedApiGet.mockImplementation(routedApiGet(
      mockCostData,
      { ...mockSavingsData, compression_pct: 4.2, conversation_cache_tokens: 1200000, squash_tokens_saved: 0 },
    ) as never)
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('stat-tokens-saved')).toBeInTheDocument()
    })
    expect(screen.getByTestId('stat-tokens-saved')).toHaveTextContent('1.2M')
  })

  // --- Filter swap speed ---

  it('filter swap paints cached per-period data BEFORE the network fetch resolves', async () => {
    // Preload localStorage for "today" so the click can paint instantly.
    const todayCached = {
      ...mockCostData,
      event_count: 99,
      agent_count: 42,
      period: 'today',
    }
    localStorage.setItem('myos_cost_data_cache_today', JSON.stringify(todayCached))
    localStorage.setItem('myos_cost_period', 'week')

    // Keep the /costs?period=today fetch hanging so we can prove the
    // paint happens without waiting for the network.
    let resolveToday!: (v: unknown) => void
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.includes('period=today')) {
        return new Promise((res) => { resolveToday = res })
      }
      return Promise.resolve(mockCostData)
    })

    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('summary-card')).toBeInTheDocument())

    // Click Today.  The fetch for period=today is still pending.
    fireEvent.click(screen.getByTestId('time-filter-today'))

    // The cached "today" numbers must appear before the network resolves.
    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument() // agent_count from cache
    })
    // Drain the pending promise so there is no unhandled rejection warning.
    resolveToday?.(todayCached)
  })

  it('writes per-period cache so swapping back is instant', async () => {
    localStorage.setItem('myos_cost_period', 'month')
    renderCostTracking()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/costs?period=month')
    })
    // After the initial load, a per-period cache entry must exist so a
    // later swap back to "month" can paint instantly from localStorage.
    await waitFor(() => {
      expect(localStorage.getItem('myos_cost_data_cache_month')).toBeTruthy()
    })
    const stored = JSON.parse(localStorage.getItem('myos_cost_data_cache_month') || '{}')
    expect(stored.event_count).toBe(mockCostData.event_count)
  })

  it('re-rendering with the same data reference does not blow away memoized derived values', async () => {
    // This is a cheap proxy for the "filter swap memoization" assertion:
    // if the same data reference produces the same DOM structure across
    // re-renders, the derived values are stable (useMemo is doing its job).
    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('summary-card')).toBeInTheDocument())

    // Grab the Usage History table element.
    const firstRow = screen.getByText('test-agent')
    const firstTable = firstRow.closest('table')
    expect(firstTable).toBeTruthy()

    // Trigger a re-render by toggling refresh.  Because the mocked fetch
    // returns the same reference, the derived memos should stay stable.
    fireEvent.click(screen.getByText('Refresh'))
    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })
    // Same data => same sorted reversed list => same row ordering.
    const rowsAfter = screen.getAllByText(/test-agent|refactor-bot|research-agent/)
    expect(rowsAfter.length).toBeGreaterThanOrEqual(3)
  })

  // -------------------------------------------------------------------
  // Explain popover tests
  // -------------------------------------------------------------------

  const mockExplainInputTokens = {
    metric: 'input_tokens',
    formula: 'raw input tokens plus cache creation tokens plus cache read tokens',
    numerator: {
      value: 810200000,
      label: 'Combined input tokens (what was actually sent to the AI)',
      source: '.ostk/audit.jsonl chat.completion events plus Claude Code transcripts in ~/.claude/projects',
    },
    denominator: {
      value: {
        raw_input_tokens: 4000000,
        cache_creation_tokens: 5000000,
        cache_read_tokens: 801200000,
      },
      label: 'Breakdown of the three components',
      source: 'same audit log and transcripts, fields: input_tokens, cache_creation_input_tokens, cache_read_input_tokens',
    },
    result: 810200000,
    result_label: '810.2M tokens',
    window: 'This Week (rolling 7 days)',
    note: 'When prompt caching is on, most of this total is reused context (cache reads), not new input.',
  }

  it('explain popover renders formula and fetches explain endpoint when clicked', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs/explain/input_tokens')) return Promise.resolve(mockExplainInputTokens)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      return Promise.resolve({})
    })

    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('input-tokens-tile')).toBeInTheDocument()
    })

    // Click the info icon to open the popover
    const trigger = screen.getByTestId('explain-trigger-input_tokens')
    fireEvent.click(trigger)

    // Wait for the popover to populate from the mocked /explain call
    await waitFor(() => {
      expect(screen.getByTestId('explain-popover-input_tokens')).toBeInTheDocument()
    })

    // Formula text is shown
    expect(screen.getByText(/raw input tokens plus cache creation tokens plus cache read tokens/)).toBeInTheDocument()
    // Result label is shown
    expect(screen.getByTestId('explain-result-input_tokens')).toHaveTextContent('810.2M tokens')
    // Source is shown
    expect(screen.getByText(/audit\.jsonl chat\.completion events/)).toBeInTheDocument()
    // Window label is shown inside the popover
    const popover = screen.getByTestId('explain-popover-input_tokens')
    expect(popover).toHaveTextContent(/This Week/)
    // The fetch was keyed on the current period (default "week")
    expect(mockedApiGet).toHaveBeenCalledWith(expect.stringMatching(/\/costs\/explain\/input_tokens\?period=/))
  })

  it('explain popover closes on Escape key', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs/explain/agents_spawned')) return Promise.resolve({
        metric: 'agents_spawned',
        formula: 'count of agent.spawned events',
        numerator: { value: 2654, label: 'Agent spawn events', source: '.ostk/audit.jsonl' },
        denominator: null,
        result: 2654,
        result_label: '2,654 agents',
        window: 'This Week (rolling 7 days)',
        note: null,
      })
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      return Promise.resolve({})
    })

    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })

    const trigger = screen.getByTestId('explain-trigger-agents_spawned')
    fireEvent.click(trigger)

    await waitFor(() => {
      expect(screen.getByTestId('explain-popover-agents_spawned')).toBeInTheDocument()
    })

    // Escape closes the popover
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('explain-popover-agents_spawned')).not.toBeInTheDocument()
    })
  })

  it('every targeted metric has an explain trigger', async () => {
    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('summary-card')).toBeInTheDocument()
    })
    // The ostk savings tiles (compression, tokens_saved, time_saved) only
    // render when data is available. With mockSavingsData they do.
    await waitFor(() => {
      expect(screen.getByTestId('ostk-savings-card')).toBeInTheDocument()
    })

    // Must be accessible via the ExplainPopover
    expect(screen.getByTestId('explain-trigger-agents_spawned')).toBeInTheDocument()
    expect(screen.getByTestId('explain-trigger-input_tokens')).toBeInTheDocument()
    expect(screen.getByTestId('explain-trigger-output_tokens')).toBeInTheDocument()
    expect(screen.getByTestId('explain-trigger-context_reuse_pct')).toBeInTheDocument()
    expect(screen.getByTestId('explain-trigger-context_compression')).toBeInTheDocument()
    expect(screen.getByTestId('explain-trigger-tokens_saved')).toBeInTheDocument()
  })

  // Regression: the Context reuse rate popover sat forever on
  // "Loading the math." because its context_reuse_pct tile was only
  // rendered when cacheHitPct was not null. When the parent payload
  // had the field, the tile (and its trigger) appeared. Clicking the
  // trigger must fetch /costs/explain/context_reuse_pct and render the
  // real formula and result, never the loading placeholder.
  it('context reuse rate popover renders real math (not "Loading the math.") after click', async () => {
    const mockExplainReuse = {
      metric: 'context_reuse_pct',
      formula: 'cache read tokens divided by (raw input tokens plus cache creation tokens plus cache read tokens), as a percentage',
      numerator: {
        value: 4992110291,
        label: 'Cache read tokens (served from the prompt cache)',
        source: '.ostk/audit.jsonl chat.completion events plus Claude Code transcripts, field: cache_read_input_tokens',
      },
      denominator: {
        value: 5095979398,
        label: 'Total input tokens (raw + cache creation + cache read)',
        source: 'same events, sum of all three token fields',
      },
      result: 97.9,
      result_label: '97.9%',
      window: 'This Week (rolling 7 days)',
      note: 'Prompt caching re-sends the full transcript each turn.',
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs/explain/context_reuse_pct')) return Promise.resolve(mockExplainReuse)
      if (path.startsWith('/costs')) return Promise.resolve({ ...mockCostData, context_reuse_pct: 97.9 })
      return Promise.resolve({})
    })

    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('explain-trigger-context_reuse_pct')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('explain-trigger-context_reuse_pct'))

    await waitFor(() => {
      expect(screen.getByTestId('explain-popover-context_reuse_pct')).toBeInTheDocument()
    })

    // The actual math must render. No "Loading the math." placeholder.
    await waitFor(() => {
      expect(screen.getByTestId('explain-result-context_reuse_pct')).toHaveTextContent('97.9%')
    })
    expect(screen.getByText(/cache read tokens divided by/)).toBeInTheDocument()
    expect(screen.queryByText('Loading the math.')).not.toBeInTheDocument()
    // Source string with a number from the payload is in the DOM.
    const popover = screen.getByTestId('explain-popover-context_reuse_pct')
    expect(popover).toHaveTextContent(/4,992,110,291/)
  })

  // Regression (→20): second open of the same popover must NOT show
  // "Loading the math.". The stale value should appear immediately while
  // the background refresh runs.
  it('explain popover shows cached data immediately on second open (no loading flash)', async () => {
    const mockExplainReuse = {
      metric: 'context_reuse_pct',
      formula: 'cache read tokens divided by total input tokens',
      numerator: { value: 100, label: 'Cache read', source: 'audit.jsonl' },
      denominator: { value: 200, label: 'Total input', source: 'audit.jsonl' },
      result: 50.0,
      result_label: '50.0%',
      window: 'This Week (rolling 7 days)',
      note: null,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs/explain/context_reuse_pct')) return Promise.resolve(mockExplainReuse)
      if (path.startsWith('/costs')) return Promise.resolve({ ...mockCostData, context_reuse_pct: 50.0 })
      return Promise.resolve({})
    })

    renderCostTracking()
    await waitFor(() => {
      expect(screen.getByTestId('explain-trigger-context_reuse_pct')).toBeInTheDocument()
    })

    // First open: fetch runs, data appears.
    fireEvent.click(screen.getByTestId('explain-trigger-context_reuse_pct'))
    await waitFor(() => {
      expect(screen.getByTestId('explain-result-context_reuse_pct')).toHaveTextContent('50.0%')
    })

    // Close the popover.
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByTestId('explain-popover-context_reuse_pct')).not.toBeInTheDocument()
    })

    // Second open: must show data immediately, never "Loading the math."
    fireEvent.click(screen.getByTestId('explain-trigger-context_reuse_pct'))
    await waitFor(() => {
      expect(screen.getByTestId('explain-popover-context_reuse_pct')).toBeInTheDocument()
    })
    // "Loading the math." must never appear. The stale payload is shown right away.
    expect(screen.queryByText('Loading the math.')).not.toBeInTheDocument()
    expect(screen.getByTestId('explain-result-context_reuse_pct')).toHaveTextContent('50.0%')
  })

  // Regression (tooltip viewport clip): when the explain-popover trigger is
  // near the top of the viewport (i.e. not enough room above for the ~420px
  // popover), the popover must flip BELOW the trigger. Otherwise its top
  // edge gets clipped by the browser chrome / address bar.
  it('explain popover flips below the trigger when there is not enough room above', async () => {
    const mockExplainTokensSaved = {
      metric: 'tokens_saved',
      formula: 'conversation cache tokens plus squash tokens saved',
      numerator: { value: 204000, label: 'Tokens saved', source: '/costs/savings' },
      denominator: null,
      result: 204000,
      result_label: '204,000 tokens',
      window: 'This Week',
      note: null,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs/explain/tokens_saved')) return Promise.resolve(mockExplainTokensSaved)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      return Promise.resolve({})
    })

    // Force jsdom to report a viewport that is shallow up top: the trigger
    // sits at y=40, so spaceAbove=40 which is far less than the 420px the
    // popover needs. Expected behavior: position='below', meaning the
    // popover's inline style uses `top` (not `bottom`) and that top is
    // clamped to at least 8.
    const originalGetRect = Element.prototype.getBoundingClientRect
    Element.prototype.getBoundingClientRect = function () {
      // The trigger button carries data-testid="explain-trigger-..." so we
      // return a near-top rect for it. Everything else gets the default.
      if (this instanceof HTMLElement && this.getAttribute?.('data-testid')?.startsWith('explain-trigger-')) {
        return {
          top: 40, bottom: 54, left: 200, right: 214, width: 14, height: 14, x: 200, y: 40,
          toJSON() { return this },
        } as DOMRect
      }
      return originalGetRect.call(this) as DOMRect
    }
    // Give jsdom a real viewport height so window.innerHeight is meaningful.
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true })
    Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true })

    try {
      renderCostTracking()
      await waitFor(() => {
        expect(screen.getByTestId('explain-trigger-tokens_saved')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByTestId('explain-trigger-tokens_saved'))

      const popover = await screen.findByTestId('explain-popover-tokens_saved')
      // Flipped-below means the popover positions with `top`, not `bottom`.
      const topStr = (popover as HTMLElement).style.top
      const bottomStr = (popover as HTMLElement).style.bottom
      expect(topStr).not.toBe('')
      expect(bottomStr).toBe('')
      // And that top must be safely inside the viewport (>= 0, clamped to 8).
      const topPx = parseFloat(topStr)
      expect(topPx).toBeGreaterThanOrEqual(0)
    } finally {
      Element.prototype.getBoundingClientRect = originalGetRect
    }
  })
})

const mockUsageData = {
  claude: {
    auth_source: 'subscription',
    messages_today: 8,
    tokens_today: 9600,
    total_messages: 30,
    recent_daily: [
      { date: '2026-05-09', messages: 4, input_tokens: 20000, output_tokens: 4000 },
      { date: '2026-05-10', messages: 7, input_tokens: 30000, output_tokens: 6000 },
      { date: '2026-05-11', messages: 6, input_tokens: 25000, output_tokens: 5000 },
      { date: '2026-05-12', messages: 8, input_tokens: 15000, output_tokens: 3000 },
      { date: '2026-05-13', messages: 10, input_tokens: 10000, output_tokens: 2000 },
    ],
    session_tokens: { cache_read: 267061, output: 3467, billed: 393521, cache_hit_rate_pct: 67.9 },
    quota_available: false,
    quota_note: 'Subscription quota not available without Anthropic account API auth.',
  },
  gemini: {
    auth_source: 'gemini_cli',
    messages_today: 2,
    tokens_today: 2400,
    total_messages: 5,
    recent_daily: [
      { date: '2026-05-09', messages: 0, input_tokens: 0, output_tokens: 0 },
      { date: '2026-05-10', messages: 1, input_tokens: 5000, output_tokens: 1000 },
      { date: '2026-05-11', messages: 0, input_tokens: 0, output_tokens: 0 },
      { date: '2026-05-12', messages: 2, input_tokens: 3000, output_tokens: 600 },
      { date: '2026-05-13', messages: 2, input_tokens: 2000, output_tokens: 400 },
    ],
    quota_available: false,
    quota_note: 'Gemini CLI has no native quota command.',
  },
}

describe('CostTracking — Subscription tab (→1299)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useAppStore.setState({ chatOpen: false, osName: 'yourOS', darkMode: true, showBudgetCaps: false })
    mockedApiGet.mockImplementation((path: string) => {
      if (path.startsWith('/costs/savings')) return Promise.resolve(mockSavingsData)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/usage') return Promise.resolve(mockUsageData)
      return Promise.resolve({})
    })
  })

  it('shows a Subscription tab', async () => {
    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('tab-subscription')).toBeInTheDocument())
  })

  it('renders Claude and Gemini cards on click', async () => {
    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('tab-subscription')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('tab-subscription'))
    await waitFor(() => expect(screen.getByTestId('subscription-panel')).toBeInTheDocument())
    expect(screen.getByTestId('provider-card-claude')).toBeInTheDocument()
    expect(screen.getByTestId('provider-card-gemini')).toBeInTheDocument()
  })

  it('shows messages today for Claude', async () => {
    renderCostTracking()
    await waitFor(() => expect(screen.getByTestId('tab-subscription')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('tab-subscription'))
    await waitFor(() => expect(screen.getByTestId('provider-card-claude')).toBeInTheDocument())
    expect(screen.getByTestId('provider-card-claude')).toHaveTextContent('8')
  })
})
