import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from './Dashboard'
import { useAppStore, DEFAULT_DASHBOARD_WIDGETS } from '../stores/app'

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
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

const mockDashboardData = {
  counts: { open: 5, closed: 12, p0: 1, p1: 3, p2: 1 },
  focus: [
    { title: 'Fix login flow', id: '\u2192123', priority: 'P0' },
  ],
  recent_tasks: [
    { id: '\u2192123', title: 'Fix login flow', priority: 'P0' },
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

const mockClockData = {
  kernel: 'v2.2.9 (@prime+0)',
  session: '3h12m',
  wall: '2026-04-20T17:46:04Z',
  audit: '223 events',
  swap: '~ stale (12h29m)',
  focus: '',
}

const mockSessionDiff = {
  files_changed: [],
  needles_filed: [],
  audit_events: [],
  audit_total: 0,
}

const mockCompoundsData = {
  top: null,
  all: [],
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
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
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
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/Nothing to summarize yet/)).toBeInTheDocument()
    })
  })

  it('has a Refresh button on the Day Summary card', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
    // Today's Focus no longer has a refresh button (auto-refreshes every 15s)
    // so there is only one Refresh button, on the Day Summary card.
    const refreshButtons = screen.getAllByText('Refresh')
    expect(refreshButtons.length).toBeGreaterThanOrEqual(1)
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

describe("Today's Focus deep-link", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('clicking a focus task row navigates to that specific task via ?focus=id', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Fix login flow')).toBeInTheDocument()
    })

    const row = screen.getByRole('button', { name: /Open task Fix login flow/i })
    fireEvent.click(row)

    // encodeURIComponent('→123') yields '%E2%86%92123'
    expect(mockNavigate).toHaveBeenCalledWith('/tasks?focus=%E2%86%92123')
    expect(mockNavigate).not.toHaveBeenCalledWith('/tasks')
  })

  it('pressing Enter on a focused row navigates to that task', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Fix login flow')).toBeInTheDocument()
    })

    const row = screen.getByRole('button', { name: /Open task Fix login flow/i })
    fireEvent.keyDown(row, { key: 'Enter' })

    expect(mockNavigate).toHaveBeenCalledWith('/tasks?focus=%E2%86%92123')
  })
})

describe('Quick Launch inline modals', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('clicking the New Task tile opens the QuickAddTaskModal inline', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Quick Launch')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /New Task/i }))

    // The modal heading should appear in place, no navigation happens.
    expect(screen.getByRole('dialog', { name: /Add a new task/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'New task' })).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith('/tasks')
  })

  it('clicking the Spawn Agent tile opens the QuickSpawnAgentModal inline', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Quick Launch')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Spawn Agent/i }))

    expect(screen.getByRole('dialog', { name: /Spawn a new agent/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Spawn agent' })).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith('/agents')
  })

  it('clicking the Capture Idea tile opens the QuickCaptureIdeaModal inline', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Quick Launch')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Capture Idea/i }))

    expect(screen.getByRole('dialog', { name: /Capture a new idea/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Capture idea' })).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith('/ideas')
  })
})

describe('Briefing startup retry (needle 315)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS],
    })
    localStorage.setItem('myos-tour-complete', 'true')
  })

  it('retries up to 3 times before showing unavailable', async () => {
    vi.useFakeTimers()
    let briefingCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') {
        briefingCalls++
        return Promise.reject(new Error('connection refused'))
      }
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    renderDashboard()

    // Initial call fires on mount
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(briefingCalls).toBe(1)

    // Three retries at 2s intervals
    await vi.advanceTimersByTimeAsync(2000)
    expect(briefingCalls).toBe(2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(briefingCalls).toBe(3)
    await vi.advanceTimersByTimeAsync(2000)
    expect(briefingCalls).toBe(4) // initial + 3 retries

    // After exhausting retries, shows unavailable
    await vi.advanceTimersByTimeAsync(1)
    expect(screen.getByText(/temporarily unavailable/i)).toBeInTheDocument()

    vi.useRealTimers()
  })

  it('recovers when a retry succeeds after initial failures', async () => {
    vi.useFakeTimers()
    let briefingCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') {
        briefingCalls++
        if (briefingCalls <= 2) {
          return Promise.reject(new Error('connection refused'))
        }
        return Promise.resolve({ show: true, briefing: 'Good morning!' })
      }
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    renderDashboard()

    // First two calls fail
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(briefingCalls).toBe(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(briefingCalls).toBe(2)

    // Third call succeeds
    await vi.advanceTimersByTimeAsync(2000)
    expect(briefingCalls).toBe(3)

    await vi.advanceTimersByTimeAsync(1)
    expect(screen.getByText('Good morning!')).toBeInTheDocument()
    expect(screen.queryByText(/temporarily unavailable/i)).toBeNull()

    vi.useRealTimers()
  })
})

describe('Dashboard widget customization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS],
    })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('renders every default widget when the preference is the full list', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText("Today's Focus")).toBeInTheDocument()
      expect(screen.getByText('Quick Launch')).toBeInTheDocument()
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
  })

  it('renders every widget id from DEFAULT_DASHBOARD_WIDGETS even when API data is empty', async () => {
    // Regression for the briefing card bug. The customize modal lists
    // every widget in DEFAULT_DASHBOARD_WIDGETS as a toggle, but the
    // dashboard render functions used to return null when their data
    // source was empty. Toggling a card on did nothing visible. Every
    // render function in Dashboard.tsx must now produce a DOM node with
    // its widget testid even when the API returns empty data so the
    // user always sees a placeholder instead of a missing card.
    // Empty-but-shaped data for every endpoint. The dashboard should
    // render every widget's empty state when its data source is empty.
    const emptyDashboard = {
      counts: { open: 0, closed: 0, p0: 0, p1: 0, p2: 0 },
      focus: [],
      recent_tasks: [],
      hay_count: 0,
      ostk_status: 'no daemon running',
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(emptyDashboard)
      if (path === '/dashboard/summary') return Promise.resolve({ bullets: [] })
      if (path === '/dashboard/compounds') return Promise.resolve({ top: null, items: [] })
      if (path === '/dashboard/diff') return Promise.resolve({ added: [], removed: [] })
      if (path.startsWith('/costs')) return Promise.resolve({ entries: [] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    useAppStore.setState({
      dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS],
    })
    renderDashboard()

    // Every widget in the canonical list MUST render its testid, even
    // when the API has nothing to show. If any of these fails, a render
    // function is silently returning null and the toggle is broken.
    const expectedTestIds = [
      'widget-briefing',
      'widget-focus-first',
      'widget-todays-focus',
      'widget-quick-launch',
      'widget-next-meeting',
      'widget-day-summary',
    ]
    for (const testId of expectedTestIds) {
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => {
        expect(screen.getByTestId(testId)).toBeInTheDocument()
      })
    }
  })

  it('hides widgets that are not in the dashboardWidgets preference', async () => {
    useAppStore.setState({
      dashboardWidgets: ['todays_focus'],
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText("Today's Focus")).toBeInTheDocument()
    })
    // Quick Launch and Day Summary should be gone.
    expect(screen.queryByText('Quick Launch')).toBeNull()
    expect(screen.queryByText('Day Summary')).toBeNull()
  })

  it('renders visible grid widgets in the saved order', async () => {
    useAppStore.setState({
      dashboardWidgets: ['quick_launch', 'todays_focus', 'day_summary'],
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-quick-launch')).toBeInTheDocument()
      expect(screen.getByTestId('widget-todays-focus')).toBeInTheDocument()
      expect(screen.getByTestId('widget-day-summary')).toBeInTheDocument()
    })

    // DOM order must match the saved preference order.
    const cards = screen.getAllByTestId(/^widget-(quick-launch|todays-focus|day-summary)$/)
    expect(cards.map((el) => el.dataset.testid)).toEqual([
      'widget-quick-launch',
      'widget-todays-focus',
      'widget-day-summary',
    ])
  })

  it('has a Customize button that opens the customize modal', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Quick Launch')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    expect(
      screen.getByRole('dialog', { name: /Customize dashboard/i }),
    ).toBeInTheDocument()
  })

  // Regression for the Customize dashboard toggle bug: users toggled a
  // hidden card on and saved, but the card never appeared on the
  // dashboard. The root cause was that conditional render functions
  // returned null when there was no data (no next meeting, no compound
  // task), so the toggle looked broken even though the id was saved.
  it('toggling a hidden card on via the modal adds it to the dashboard', async () => {
    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()
    await waitFor(() => expect(screen.getByText("Today's Focus")).toBeInTheDocument())
    expect(screen.queryByText('Quick Launch')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    const dialog = screen.getByRole('dialog', { name: /Customize dashboard/i })
    const sw = within(dialog).getByRole('switch', { name: /Show Quick Launch/i })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(sw)
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }))

    await waitFor(() => {
      expect(screen.getByTestId('widget-quick-launch')).toBeInTheDocument()
    })
    expect(useAppStore.getState().dashboardWidgets).toContain('quick_launch')
  })

  it('toggling on Next Meeting shows an empty-state card when there are no meetings', async () => {
    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()
    await waitFor(() => expect(screen.getByText("Today's Focus")).toBeInTheDocument())
    expect(screen.queryByTestId('widget-next-meeting')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    const dialog = screen.getByRole('dialog', { name: /Customize dashboard/i })
    fireEvent.click(within(dialog).getByRole('switch', { name: /Show Next Meeting/i }))
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }))

    await waitFor(() => {
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
    })
    // Empty state copy should appear when there are no upcoming meetings.
    expect(screen.getByText(/No upcoming meetings/i)).toBeInTheDocument()
  })

  it('toggling on Focus on this first shows an empty-state card when there are no blocking tasks', async () => {
    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()
    await waitFor(() => expect(screen.getByText("Today's Focus")).toBeInTheDocument())
    expect(screen.queryByTestId('widget-focus-first')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    const dialog = screen.getByRole('dialog', { name: /Customize dashboard/i })
    fireEvent.click(within(dialog).getByRole('switch', { name: /Show Focus on this first/i }))
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }))

    await waitFor(() => {
      expect(screen.getByTestId('widget-focus-first')).toBeInTheDocument()
    })
    expect(screen.getByText(/Nothing blocking others right now/i)).toBeInTheDocument()
  })
})

