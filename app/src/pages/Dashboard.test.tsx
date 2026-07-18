import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from './Dashboard'
import { useAppStore, DEFAULT_DASHBOARD_WIDGETS } from '../stores/app'
import { ADVENTURE_DISMISSED_KEY } from '../lib/adventures'

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
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false, dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS] })
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

  it("has a Refresh summary option in the Today's Focus widget menu", async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
    // Refresh lives in the Today's Focus three-dot menu now that the
    // Day Summary card is folded into Today's Focus
    fireEvent.click(screen.getByTestId('widget-menu-trigger-todays_focus'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-todays-focus-refresh')).toBeInTheDocument()
    })
  })

  it('calls the summary API again when Refresh is clicked from the menu', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })

    const summaryCallsBefore = mockedApiGet.mock.calls.filter(
      (c) => c[0] === '/dashboard/summary'
    ).length

    // Refresh lives in the Today's Focus three-dot menu
    fireEvent.click(screen.getByTestId('widget-menu-trigger-todays_focus'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-todays-focus-refresh')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('widget-menu-todays-focus-refresh'))

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

  it('renders skeleton blocks while briefing is loading', () => {
    // Keep briefing in loading state by never resolving the /briefing call
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      // /briefing never resolves so briefingLoading stays true
      return new Promise(() => {})
    })

    renderDashboard()

    // The skeleton lines should be visible while briefing loads
    const skeletonLines = document.querySelectorAll('[data-testid="skeleton-line"]')
    expect(skeletonLines.length).toBeGreaterThan(0)
  })
})

describe("Today's Focus deep-link", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false, dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS] })
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

describe("Today's Focus line-clamp", () => {
  const longTitle =
    'Plan Waves backend pieces: /api/agents/spawn-preflight endpoint + builder-prompt agent-side conflict re-check. ' +
    'Frontend rebuild landed in commit bd04785. PlanWavesPanel + Specs.tsx already call lib/spawn.buildSpec which ' +
    'handles 409 lock_conflict via ConflictDialog. The kernel returns 409 only when the actual spawn collides. ' +
    'The original plan added two more layers: NEW endpoint GET /api/agents/spawn-preflight?paths=... Always 200. ' +
    'Update the builder-spawn prompt to prepend a mandatory first step before reading or editing anything.'

  const mockDashboardDataLong = {
    ...mockDashboardData,
    focus: [{ title: longTitle, id: '→1465', priority: 'P2' }],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false, dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS] })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardDataLong)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('renders long task title with line-clamp class', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(longTitle)).toBeInTheDocument()
    })
    const titleEl = screen.getByText(longTitle)
    expect(titleEl.className).toMatch(/line-clamp-2/)
  })

  it('shows a "Show more" button for a long task title', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('focus-task-expand')).toBeInTheDocument()
    })
    expect(screen.getByTestId('focus-task-expand')).toHaveTextContent('Show more')
  })

  it('removes line-clamp after clicking "Show more"', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('focus-task-expand')).toBeInTheDocument()
    })

    const titleEl = screen.getByText(longTitle)
    expect(titleEl.className).toMatch(/line-clamp-2/)

    fireEvent.click(screen.getByTestId('focus-task-expand'))

    expect(titleEl.className).not.toMatch(/line-clamp-2/)
    expect(screen.getByTestId('focus-task-expand')).toHaveTextContent('Show less')
  })
})

describe('Quick Launch inline modals', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false, dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS] })
    localStorage.setItem('myos-tour-complete', 'true')

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/adventures/templates') return Promise.resolve({ adventures: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
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
      expect(screen.getByTestId('widget-quick-launch')).toBeInTheDocument()
    })

    // Use within to scope to the Quick Launch widget and avoid
    // matching the "Spawn agent" button in the Adventure card.
    const quickLaunch = screen.getByTestId('widget-quick-launch')
    fireEvent.click(within(quickLaunch).getByRole('button', { name: /Spawn Agent/i }))

    expect(screen.getByRole('dialog', { name: /Spawn a new agent/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Spawn agent' })).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith('/agents')
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
    // Quick Launch should be gone. Day Summary renders inside Today's
    // Focus now, so it stays visible whenever that card is on.
    expect(screen.queryByText('Quick Launch')).toBeNull()
    expect(screen.getByText('Day Summary')).toBeInTheDocument()
  })

  it('renders visible grid widgets in the saved order', async () => {
    useAppStore.setState({
      dashboardWidgets: ['quick_launch', 'todays_focus', 'next_meeting'],
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-quick-launch')).toBeInTheDocument()
      expect(screen.getByTestId('widget-todays-focus')).toBeInTheDocument()
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
    })

    // DOM order must match the saved preference order.
    const cards = screen.getAllByTestId(/^widget-(quick-launch|todays-focus|next-meeting)$/)
    expect(cards.map((el) => el.dataset.testid)).toEqual([
      'widget-quick-launch',
      'widget-todays-focus',
      'widget-next-meeting',
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

  it('toggling on Next Event shows an empty-state card when there are no meetings', async () => {
    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()
    await waitFor(() => expect(screen.getByText("Today's Focus")).toBeInTheDocument())
    expect(screen.queryByTestId('widget-next-meeting')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    const dialog = screen.getByRole('dialog', { name: /Customize dashboard/i })
    fireEvent.click(within(dialog).getByRole('switch', { name: /Show Next Event/i }))
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }))

    await waitFor(() => {
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
    })
    // Widget renders the month grid (default range) even with no events.
    expect(screen.getByTestId('cal-grid-month')).toBeInTheDocument()
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

describe("Today's Focus session task filter", () => {
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

  it('shows only non-session tasks in Today\'s Focus and reflects correct open count', async () => {
    // The backend now filters session tasks before returning counts and focus.
    // This test verifies the frontend renders what the backend sends correctly:
    // 3 session tasks excluded, 2 real tasks shown, open count = 2.
    const filteredDashboardData = {
      counts: { open: 2, closed: 0, p0: 1, p1: 1, p2: 0 },
      focus: [
        { title: 'Fix the onboarding flow', id: '\u2192200', priority: 'P0' },
        { title: 'Update cost tracking UI', id: '\u2192201', priority: 'P1' },
      ],
      recent_tasks: [
        { id: '\u2192200', title: 'Fix the onboarding flow', priority: 'P0' },
        { id: '\u2192201', title: 'Update cost tracking UI', priority: 'P1' },
      ],
      hay_count: 0,
      ostk_status: 'no daemon running',
    }

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(filteredDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve({ bullets: [] })
      if (path === '/dashboard/compounds') return Promise.resolve({ top: null, all: [] })
      if (path === '/dashboard/diff') return Promise.resolve({ files_changed: [], needles_filed: [], audit_events: [], audit_total: 0 })
      if (path.startsWith('/costs')) return Promise.resolve({ total_budget: 0, agent_count: 0 })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByTestId('widget-todays-focus')).toBeInTheDocument()
    })

    // The two real tasks appear
    expect(screen.getByText('Fix the onboarding flow')).toBeInTheDocument()
    expect(screen.getByText('Update cost tracking UI')).toBeInTheDocument()

    // Session task titles must NOT appear
    expect(screen.queryByText(/Claude Code session claude-code-/i)).toBeNull()

    // Open count badge shows 2, not 17
    const focusWidget = screen.getByTestId('widget-todays-focus')
    expect(within(focusWidget).getByText('2 open')).toBeInTheDocument()
  })

  it('count badge matches the rendered focus list length (regression)', async () => {
    // Regression: the header once read "2 open" while the body said
    // "No focus tasks right now." because the body filtered to P0/P1
    // while the count included every open task. The backend now sends
    // all open tasks in `focus` sorted by priority, capped at 4, so
    // the count and the list describe the same set.
    const alignedData = {
      counts: { open: 2, closed: 485, p0: 0, p1: 0, p2: 2 },
      focus: [
        { title: 'Write quarterly update', id: '\u2192501', priority: 'P2' },
        { title: 'Chase vendor follow-up', id: '\u2192502', priority: 'P2' },
      ],
      recent_tasks: [
        { id: '\u2192501', title: 'Write quarterly update', priority: 'P2' },
        { id: '\u2192502', title: 'Chase vendor follow-up', priority: 'P2' },
      ],
      hay_count: 0,
      ostk_status: 'no daemon running',
    }

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(alignedData)
      if (path === '/dashboard/summary') return Promise.resolve({ bullets: [] })
      if (path === '/dashboard/compounds') return Promise.resolve({ top: null, all: [] })
      if (path === '/dashboard/diff') return Promise.resolve({ files_changed: [], needles_filed: [], audit_events: [], audit_total: 0 })
      if (path.startsWith('/costs')) return Promise.resolve({ total_budget: 0, agent_count: 0 })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    useAppStore.setState({ dashboardWidgets: ['todays_focus'] })
    renderDashboard()

    const focusWidget = await waitFor(() => screen.getByTestId('widget-todays-focus'))
    // Header says 2 open.
    expect(within(focusWidget).getByText('2 open')).toBeInTheDocument()
    // Body lists both of those 2 tasks (not the empty state).
    expect(within(focusWidget).getByText('Write quarterly update')).toBeInTheDocument()
    expect(within(focusWidget).getByText('Chase vendor follow-up')).toBeInTheDocument()
    expect(within(focusWidget).queryByText(/No focus tasks right now/i)).toBeNull()
  })
})

const MOCK_ADVENTURES = {
  adventures: [
    { id: 'build_website', title: 'Build a website', tagline: 'From idea to live site.', icon: 'language', placeholder: 'e.g. A recipe site' },
    { id: 'plan_project', title: 'Plan a project', tagline: 'Turn a fuzzy idea into a plan.', icon: 'bolt', placeholder: 'e.g. Launch a newsletter' },
    { id: 'learn_skill', title: 'Learn something new', tagline: 'A starter path.', icon: 'school', placeholder: 'e.g. Learn Spanish' },
    { id: 'off_plate', title: 'Get something off your plate', tagline: 'Break it into steps.', icon: 'task_alt', placeholder: 'e.g. Do my taxes' },
  ],
}

describe('Dashboard - Adventure card', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    localStorage.removeItem(ADVENTURE_DISMISSED_KEY)
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: ['adventure', 'todays_focus'],
      setDashboardWidgets: vi.fn(),
    })
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/adventures/templates') return Promise.resolve(MOCK_ADVENTURES)
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
      if (path === '/secrets/key-status') return Promise.resolve({ google_connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  // →2920: the "One thing to try right now" suggestion moved out of the
  // onboarding wizard and into this widget.
  function mockWithConnections(google: boolean, atlassian: boolean) {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/secrets/key-status') return Promise.resolve({ google_connected: google })
      if (path === '/atlassian/status') return Promise.resolve({ connected: atlassian })
      if (path === '/adventures/templates') return Promise.resolve(MOCK_ADVENTURES)
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  }

  it('shows the try-right-now suggestion inside the adventure widget (→2920)', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-try-now')).toBeInTheDocument()
    })
    const widget = screen.getByTestId('widget-adventure')
    expect(within(widget).getByText('One thing to try right now')).toBeInTheDocument()
    // Nothing connected: the default tasks-and-agents suggestion shows.
    expect(screen.getByTestId('adventure-try-now-text')).toHaveTextContent(/tasks and agents/i)
  })

  it('try-right-now shows the meeting-prep prompt when Google is connected', async () => {
    mockWithConnections(true, false)
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-try-now')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-try-now-text')).toHaveTextContent(/meetings/i)
  })

  it('try-right-now shows the cross-tool prompt when two sources are connected', async () => {
    mockWithConnections(true, true)
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-try-now')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-try-now-text')).toHaveTextContent(/search across/i)
  })

  it('clicking the try-right-now suggestion pre-fills chat and opens it', async () => {
    const setChatPrefill = vi.fn()
    const setChatOpen = vi.fn()
    useAppStore.setState({
      setChatPrefill: setChatPrefill as unknown as (v: string | null) => void,
      setChatOpen: setChatOpen as unknown as (v: boolean) => void,
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-try-now-btn')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-try-now-btn'))
    expect(setChatPrefill).toHaveBeenCalledWith(expect.stringContaining('tasks'))
    expect(setChatOpen).toHaveBeenCalledWith(true)
  })

  it('try-right-now still shows the default suggestion when status checks fail', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/secrets/key-status') return Promise.reject(new Error('network'))
      if (path === '/atlassian/status') return Promise.reject(new Error('network'))
      if (path === '/adventures/templates') return Promise.resolve(MOCK_ADVENTURES)
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-try-now')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-try-now-text')).toHaveTextContent(/tasks and agents/i)
  })

  it('try-right-now does not render for a dismissed adventure card', async () => {
    localStorage.setItem(ADVENTURE_DISMISSED_KEY, 'true')
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/dashboard')
    })
    expect(screen.queryByTestId('adventure-try-now')).not.toBeInTheDocument()
  })

  it('renders the adventure card when widget is in dashboardWidgets', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-adventure')).toBeInTheDocument()
    })
  })

  it('fetches adventure templates from /adventures/templates', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/adventures/templates')
    })
  })

  it('shows all four sample adventure cards after loading', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-card-plan_project')).toBeInTheDocument()
    expect(screen.getByTestId('adventure-card-learn_skill')).toBeInTheDocument()
    expect(screen.getByTestId('adventure-card-off_plate')).toBeInTheDocument()
  })

  it('selecting a card highlights it', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    const card = screen.getByTestId('adventure-card-build_website')
    fireEvent.click(card)
    expect(screen.getByTestId('adventure-card-build_website').className).toContain('border-indigo-500')
  })

  it('clicking the same card twice deselects it', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    const card = screen.getByTestId('adventure-card-build_website')
    fireEvent.click(card)
    expect(card.className).toContain('border-indigo-500')
    fireEvent.click(card)
    expect(screen.getByTestId('adventure-card-build_website').className).not.toContain('border-indigo-500')
  })

  it('spawn button is disabled when nothing is selected and description is empty', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-spawn-button')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-spawn-button')).toBeDisabled()
  })

  it('spawn button is enabled when a card is selected', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))
    expect(screen.getByTestId('adventure-spawn-button')).not.toBeDisabled()
  })

  it('spawn button is enabled when description text is entered', async () => {
    const user = userEvent.setup()
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-description-input')).toBeInTheDocument()
    })
    await user.type(screen.getByTestId('adventure-description-input'), 'Build a thing')
    expect(screen.getByTestId('adventure-spawn-button')).not.toBeDisabled()
  })

  it('clicking Spawn calls /adventures/start and shows the confirmation banner', async () => {
    vi.mocked(api.post).mockResolvedValue({})
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))
    fireEvent.click(screen.getByTestId('adventure-spawn-button'))
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/adventures/start', expect.objectContaining({ adventure_id: 'build_website' }))
    })
    await waitFor(() => {
      expect(screen.getByTestId('adventure-spawned-banner')).toBeInTheDocument()
    })
  })

  it('spawned banner contains a link to the Agents page', async () => {
    vi.mocked(api.post).mockResolvedValue({})
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))
    fireEvent.click(screen.getByTestId('adventure-spawn-button'))
    await waitFor(() => {
      expect(screen.getByTestId('adventure-agents-link')).toBeInTheDocument()
    })
  })

  it('dismissing the card hides it and persists to localStorage', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-adventure')).toBeInTheDocument()
    })
    // Dismiss is now in the three-dot menu
    fireEvent.click(screen.getByTestId('widget-menu-trigger-adventure'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-adventure-dismiss')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('widget-menu-adventure-dismiss'))
    expect(screen.queryByTestId('widget-adventure')).not.toBeInTheDocument()
    expect(localStorage.getItem(ADVENTURE_DISMISSED_KEY)).toBe('true')
  })

  it('card does not render when localStorage flag is already set', async () => {
    localStorage.setItem(ADVENTURE_DISMISSED_KEY, 'true')
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/dashboard')
    })
    expect(screen.queryByTestId('widget-adventure')).not.toBeInTheDocument()
  })

  it('does not fetch adventure templates when already dismissed', async () => {
    localStorage.setItem(ADVENTURE_DISMISSED_KEY, 'true')
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/dashboard')
    })
    expect(mockedApiGet).not.toHaveBeenCalledWith('/adventures/templates')
  })
})

describe('Briefing hard-refresh reload (un-dismiss on reload)', () => {
  const originalGetEntriesByType = performance.getEntriesByType.bind(performance)

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
    vi.mocked(api.post).mockResolvedValue({})

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
      if (path === '/adventures/templates') return Promise.resolve({ adventures: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  afterEach(() => {
    performance.getEntriesByType = originalGetEntriesByType
  })

  function stubNavigationType(type: 'navigate' | 'reload' | 'back_forward' | 'prerender') {
    performance.getEntriesByType = ((entryType: string) => {
      if (entryType === 'navigation') {
        return [{ type } as unknown as PerformanceNavigationTiming]
      }
      return originalGetEntriesByType(entryType)
    }) as typeof performance.getEntriesByType
  }

  it('does NOT call /briefing/undismiss on a fresh navigation load', async () => {
    stubNavigationType('navigate')
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/briefing')
    })
    expect(api.post).not.toHaveBeenCalledWith('/briefing/undismiss', expect.anything())
  })

  it('calls /briefing/undismiss on a browser reload so dismissed briefings come back', async () => {
    stubNavigationType('reload')
    renderDashboard()
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/briefing/undismiss', {})
    })
    // And still fetches the briefing afterwards.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/briefing')
    })
  })

  it('still fetches the briefing if /briefing/undismiss fails on reload', async () => {
    stubNavigationType('reload')
    vi.mocked(api.post).mockImplementation((path: string) => {
      if (path === '/briefing/undismiss') return Promise.reject(new Error('network'))
      return Promise.resolve({})
    })
    renderDashboard()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/briefing')
    })
  })
})


// ---------------------------------------------------------------------------
// localStorage seed for briefing (primary rows paint within 300ms rule)
// ---------------------------------------------------------------------------

describe('Briefing localStorage seed', () => {
  const BRIEFING_SEED_KEY = 'myos.briefing.last'

  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve({ top: null, all: [] })
      if (path === '/dashboard/diff') return Promise.resolve({
        files_changed: [], needles_filed: [], audit_events: [], audit_total: 0,
      })
      if (path === '/costs/summary') return Promise.resolve(mockCostData)
      if (path === '/clock') return Promise.resolve(mockClockData)
      if (path === '/sessions/active') return Promise.resolve({
        sessions: [], count: 0, active_count: 0, idle_count: 0,
      })
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      return Promise.resolve({})
    })
  })

  afterEach(() => {
    localStorage.clear()
  })

  const renderDashboard = () => render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  )

  it('paints the seeded briefing immediately from localStorage before the network fetch completes', () => {
    const seededBriefing = {
      show: true,
      briefing: 'Yesterday you closed 3 tasks. Today focus on bug fixes.',
      action_items: [],
    }
    localStorage.setItem(BRIEFING_SEED_KEY, JSON.stringify({
      ts: Date.now(),
      data: seededBriefing,
    }))

    // /briefing never resolves so the only way the briefing text can
    // show up is via the localStorage seed.
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') return new Promise(() => {})
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      return Promise.resolve({})
    })

    renderDashboard()

    // Seeded briefing text is on screen synchronously after render.
    expect(screen.getByText(/Yesterday you closed 3 tasks/)).toBeInTheDocument()
  })

  it('shows a Refreshing hint while the fresh fetch is in flight on top of a seed', () => {
    const seededBriefing = {
      show: true,
      briefing: 'Seeded briefing text.',
      action_items: [],
    }
    localStorage.setItem(BRIEFING_SEED_KEY, JSON.stringify({
      ts: Date.now(),
      data: seededBriefing,
    }))

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') return new Promise(() => {})  // never resolves
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      return Promise.resolve({})
    })

    renderDashboard()

    expect(screen.getByTestId('briefing-refreshing')).toBeInTheDocument()
  })

  it('writes the briefing to localStorage when the fresh fetch succeeds', async () => {
    const freshBriefing = {
      show: true,
      briefing: 'Fresh briefing text from the server.',
      action_items: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') return Promise.resolve(freshBriefing)
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      return Promise.resolve({})
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/Fresh briefing text from the server/)).toBeInTheDocument()
    })

    const raw = localStorage.getItem(BRIEFING_SEED_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string)
    expect(parsed.data.briefing).toBe('Fresh briefing text from the server.')
    expect(typeof parsed.ts).toBe('number')
  })

  it('ignores a seed older than 24 hours', () => {
    const staleBriefing = {
      show: true,
      briefing: 'Old stale briefing text.',
      action_items: [],
    }
    // 25 hours ago.
    localStorage.setItem(BRIEFING_SEED_KEY, JSON.stringify({
      ts: Date.now() - (25 * 60 * 60 * 1000),
      data: staleBriefing,
    }))

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') return new Promise(() => {})
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      return Promise.resolve({})
    })

    renderDashboard()

    // Stale seed was not painted, we should see the loading skeleton instead.
    expect(screen.queryByText(/Old stale briefing text/)).not.toBeInTheDocument()
  })
})

describe('widget-briefing no nested <p> (hydration regression →1018)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: ['briefing'],
    })
  })

  it('briefing paragraphs do not contain a nested <p> child', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/briefing') return Promise.resolve({
        show: true,
        briefing: 'First paragraph.\n\nSecond paragraph.',
        action_items: [],
      })
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path === '/calendar/events') return Promise.resolve({ events: [] })
      return Promise.resolve({})
    })

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    const widget = await waitFor(() => screen.getByTestId('widget-briefing'))
    const ps = widget.querySelectorAll('p')
    ps.forEach((p) => {
      expect(p.querySelector('p')).toBeNull()
    })
  })
})

describe('calendar widget range selector', () => {
  // Build a stub calendar event N days from now so the dashboard's
  // in-window filter keeps the mocked event for whatever range is
  // active. Using a fixed offset of 2 hours keeps the event inside
  // every (Day, Week, Month) window we test.
  function makeCalendarEvent(id: string) {
    const start = new Date(Date.now() + 2 * 60 * 60 * 1000)
    const end = new Date(start.getTime() + 30 * 60 * 1000)
    return {
      id,
      summary: `Event ${id}`,
      start: { dateTime: start.toISOString() },
      end: { dateTime: end.toISOString() },
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    localStorage.clear()
    localStorage.setItem('myos-tour-complete', 'true')
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: ['next_meeting'],
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path.startsWith('/calendar/events')) {
        return Promise.resolve({ events: [makeCalendarEvent('A')] })
      }
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('defaults to Month and fetches /calendar/events?days=30', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
    })
    // Month button is the active selection on first render.
    const monthBtn = screen.getByTestId('calendar-range-month')
    expect(monthBtn).toHaveAttribute('aria-pressed', 'true')
    // Default Month sends ?days=30.
    const calendarCalls = mockedApiGet.mock.calls
      .map((c) => c[0])
      .filter((p) => typeof p === 'string' && p.startsWith('/calendar/events'))
    expect(calendarCalls.length).toBeGreaterThan(0)
    expect(calendarCalls).toContain('/calendar/events?days=30')
  })

  it('switching to Day re-fetches with ?days=1 and persists to localStorage', async () => {
    renderDashboard()
    await waitFor(() => expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('calendar-range-day'))

    await waitFor(() => {
      const calls = mockedApiGet.mock.calls.map((c) => c[0])
      expect(calls).toContain('/calendar/events?days=1')
    })
    expect(screen.getByTestId('calendar-range-day')).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('myos.calendar_widget_range')).toBe('day')
  })

  it('switching to Month from Week re-fetches with ?days=30 and persists to localStorage', async () => {
    renderDashboard()
    await waitFor(() => expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument())

    // First switch to Week so we have a non-Month starting state.
    fireEvent.click(screen.getByTestId('calendar-range-week'))
    await waitFor(() => {
      expect(screen.getByTestId('calendar-range-week')).toHaveAttribute('aria-pressed', 'true')
    })

    // Now switch to Month.
    fireEvent.click(screen.getByTestId('calendar-range-month'))

    await waitFor(() => {
      const calls = mockedApiGet.mock.calls.map((c) => c[0])
      expect(calls).toContain('/calendar/events?days=30')
    })
    expect(screen.getByTestId('calendar-range-month')).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('myos.calendar_widget_range')).toBe('month')
  })

  it('restores the saved range across remount', async () => {
    localStorage.setItem('myos.calendar_widget_range', 'day')
    const { unmount } = renderDashboard()
    await waitFor(() => expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument())
    expect(screen.getByTestId('calendar-range-day')).toHaveAttribute('aria-pressed', 'true')
    unmount()

    // Fresh render with the same localStorage value should still show Day.
    renderDashboard()
    await waitFor(() => expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument())
    expect(screen.getByTestId('calendar-range-day')).toHaveAttribute('aria-pressed', 'true')
    // And the fetch on the second mount should still ask for ?days=1.
    const calls = mockedApiGet.mock.calls.map((c) => c[0])
    expect(calls.filter((p) => p === '/calendar/events?days=1').length).toBeGreaterThan(0)
  })

  it('renders a color dot for each event row using colorId', async () => {
    const start = new Date(Date.now() + 2 * 60 * 60 * 1000)
    const end = new Date(start.getTime() + 30 * 60 * 1000)
    const coloredEvent = {
      id: 'colored-1',
      summary: 'Colored Event',
      start: { dateTime: start.toISOString() },
      end: { dateTime: end.toISOString() },
      colorId: '7', // Peacock → #039BE5
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path.startsWith('/calendar/events')) return Promise.resolve({ events: [coloredEvent] })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })

    renderDashboard()
    await waitFor(() => expect(screen.getByTestId('cal-grid-event-colored-1')).toBeInTheDocument())

    const list = screen.getByTestId('cal-grid-event-colored-1')
    const dot = list.querySelector('span[aria-hidden="true"]')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveStyle({ backgroundColor: '#039BE5' })
  })

  it('falls back to default blue dot when colorId is absent', async () => {
    renderDashboard()
    await waitFor(() => expect(screen.getByTestId('cal-grid-event-A')).toBeInTheDocument())

    const list = screen.getByTestId('cal-grid-event-A')
    const dot = list.querySelector('span[aria-hidden="true"]')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveStyle({ backgroundColor: '#4285F4' })
  })
})

describe('Widget three-dot menu and header overlap fix', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    localStorage.clear()
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
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  it('renders a three-dot menu trigger for each visible grid widget', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-trigger-todays_focus')).toBeInTheDocument()
    })
    expect(screen.getByTestId('widget-menu-trigger-adventure')).toBeInTheDocument()
  })

  it('adventure widget menu contains Dismiss and Hide widget options', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-trigger-adventure')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('widget-menu-trigger-adventure'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-adventure-dismiss')).toBeInTheDocument()
    })
    expect(screen.getByText('Dismiss')).toBeInTheDocument()
    expect(screen.getByText('Hide widget')).toBeInTheDocument()
  })

  it('todays_focus widget menu contains Refresh summary and Hide widget options', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-trigger-todays_focus')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('widget-menu-trigger-todays_focus'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-menu-todays-focus-refresh')).toBeInTheDocument()
    })
    expect(screen.getByText('Refresh summary')).toBeInTheDocument()
    expect(screen.getByText('Hide widget')).toBeInTheDocument()
  })

  it('adventure widget no longer has an inline dismiss button', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-adventure')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('adventure-dismiss')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// →2921: move handles and half/full width toggles on the widget chrome
// ---------------------------------------------------------------------------

// Capture the real store actions at import time. The Adventure describe
// above swaps setDashboardWidgets for a vi.fn() via setState, and zustand
// keeps that stub for every later test in this file. These tests need the
// genuine setters back so reorder and resize actually update the store.
const realSetDashboardWidgets = useAppStore.getState().setDashboardWidgets
const realSetDashboardWidgetSizes = useAppStore.getState().setDashboardWidgetSizes

describe('Widget move handles and width toggles (→2921)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    localStorage.clear()
    localStorage.setItem('myos-tour-complete', 'true')
    useAppStore.setState({
      chatOpen: false,
      osName: 'ToriOS',
      darkMode: true,
      showTour: false,
      dashboardWidgets: ['todays_focus', 'quick_launch', 'next_meeting'],
      dashboardWidgetSizes: {},
      setDashboardWidgets: realSetDashboardWidgets,
      setDashboardWidgetSizes: realSetDashboardWidgetSizes,
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/dashboard') return Promise.resolve(mockDashboardData)
      if (path === '/dashboard/summary') return Promise.resolve(mockSummaryData)
      if (path === '/dashboard/compounds') return Promise.resolve(mockCompoundsData)
      if (path === '/dashboard/diff') return Promise.resolve(mockSessionDiff)
      if (path.startsWith('/costs')) return Promise.resolve(mockCostData)
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/briefing') return Promise.resolve({ show: false, briefing: null })
      if (path.startsWith('/calendar/events')) return Promise.resolve({ events: [] })
      if (path === '/adventures/templates') return Promise.resolve({ adventures: [] })
      if (path === '/secrets/key-status') return Promise.resolve({ google_connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/sessions/active') return Promise.resolve({ sessions: [], count: 0, active_count: 0, idle_count: 0 })
      return Promise.reject(new Error(`unmocked path: ${path}`))
    })
  })

  // The column span lives on the grid's direct child (the wrapper div
  // around each widget, see the UAT note in Dashboard.tsx), so width is
  // asserted on the widget's parent element.
  function gridWrapper(testId: string): HTMLElement {
    return screen.getByTestId(testId).parentElement as HTMLElement
  }

  const gridOrder = () =>
    screen
      .getAllByTestId(/^widget-(quick-launch|todays-focus|next-meeting)$/)
      .map((el) => el.dataset.testid)

  it('a fresh install with no saved widths renders the default spans exactly (regression)', async () => {
    useAppStore.setState({
      dashboardWidgets: ['todays_focus', 'quick_launch', 'next_meeting', 'adventure'],
      dashboardWidgetSizes: {},
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
      expect(screen.getByTestId('widget-adventure')).toBeInTheDocument()
    })
    expect(gridWrapper('widget-next-meeting').className).toContain('lg:col-span-2')
    expect(gridWrapper('widget-adventure').className).toContain('lg:col-span-2')
    expect(gridWrapper('widget-todays-focus').className).not.toContain('lg:col-span-2')
    expect(gridWrapper('widget-quick-launch').className).not.toContain('lg:col-span-2')
  })

  it('width toggles carry the exact aria-labels for their current width', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-size-toggle-todays_focus')).toBeInTheDocument()
    })
    // Half widgets offer to grow, full widgets offer to shrink.
    expect(screen.getByTestId('widget-size-toggle-todays_focus')).toHaveAttribute(
      'aria-label',
      'make this widget full width',
    )
    expect(screen.getByTestId('widget-size-toggle-next_meeting')).toHaveAttribute(
      'aria-label',
      'make this widget half width',
    )
  })

  it('clicking the width toggle makes a half widget span both columns and saves the choice', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-size-toggle-todays_focus')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('widget-size-toggle-todays_focus'))

    expect(gridWrapper('widget-todays-focus').className).toContain('lg:col-span-2')
    expect(useAppStore.getState().dashboardWidgetSizes.todays_focus).toBe('full')
    // Persisted through the store: server and localStorage both hear it.
    expect(api.patch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({
        dashboard_widget_sizes: expect.objectContaining({ todays_focus: 'full' }),
      }),
    )
    const stored = JSON.parse(localStorage.getItem('myos-dashboard-widget-sizes') as string)
    expect(stored.todays_focus).toBe('full')
  })

  it('clicking the width toggle on a full widget brings it back to half', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-size-toggle-next_meeting')).toBeInTheDocument()
    })
    expect(gridWrapper('widget-next-meeting').className).toContain('lg:col-span-2')
    fireEvent.click(screen.getByTestId('widget-size-toggle-next_meeting'))
    expect(gridWrapper('widget-next-meeting').className).not.toContain('lg:col-span-2')
    expect(useAppStore.getState().dashboardWidgetSizes.next_meeting).toBe('half')
  })

  it('a saved half width for a default-full widget renders at half', async () => {
    useAppStore.setState({ dashboardWidgetSizes: { next_meeting: 'half' } })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-next-meeting')).toBeInTheDocument()
    })
    expect(gridWrapper('widget-next-meeting').className).not.toContain('lg:col-span-2')
  })

  it('the move handle is a button labeled so arrow keys move the widget', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-todays_focus')).toBeInTheDocument()
    })
    const handle = screen.getByTestId('widget-move-handle-todays_focus')
    expect(handle.tagName).toBe('BUTTON')
    expect(handle).toHaveAttribute(
      'aria-label',
      "Move Today's Focus. Press the up or down arrow keys to move this widget one slot.",
    )
  })

  it('ArrowDown on a focused handle moves the widget one slot later and persists the order', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-todays_focus')).toBeInTheDocument()
    })
    expect(gridOrder()).toEqual(['widget-todays-focus', 'widget-quick-launch', 'widget-next-meeting'])

    fireEvent.keyDown(screen.getByTestId('widget-move-handle-todays_focus'), { key: 'ArrowDown' })

    expect(useAppStore.getState().dashboardWidgets).toEqual(['quick_launch', 'todays_focus', 'next_meeting'])
    expect(gridOrder()).toEqual(['widget-quick-launch', 'widget-todays-focus', 'widget-next-meeting'])
    expect(api.patch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({
        dashboard_widgets: ['quick_launch', 'todays_focus', 'next_meeting'],
      }),
    )
  })

  it('ArrowUp on a focused handle moves the widget one slot earlier', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-quick_launch')).toBeInTheDocument()
    })
    fireEvent.keyDown(screen.getByTestId('widget-move-handle-quick_launch'), { key: 'ArrowUp' })
    expect(useAppStore.getState().dashboardWidgets).toEqual(['quick_launch', 'todays_focus', 'next_meeting'])
  })

  it('ArrowUp on the first grid widget changes nothing', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-todays_focus')).toBeInTheDocument()
    })
    fireEvent.keyDown(screen.getByTestId('widget-move-handle-todays_focus'), { key: 'ArrowUp' })
    expect(useAppStore.getState().dashboardWidgets).toEqual(['todays_focus', 'quick_launch', 'next_meeting'])
  })

  it('moving swaps only with the neighboring grid card, leaving banner widgets in place', async () => {
    useAppStore.setState({
      dashboardWidgets: ['briefing', 'quick_launch', 'todays_focus'],
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-quick_launch')).toBeInTheDocument()
    })
    fireEvent.keyDown(screen.getByTestId('widget-move-handle-quick_launch'), { key: 'ArrowDown' })
    // The briefing banner keeps its slot; the two grid cards swap.
    expect(useAppStore.getState().dashboardWidgets).toEqual(['briefing', 'todays_focus', 'quick_launch'])
  })

  it('a dashboard reorder shows up in the customize modal, one shared list', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByTestId('widget-move-handle-todays_focus')).toBeInTheDocument()
    })
    fireEvent.keyDown(screen.getByTestId('widget-move-handle-todays_focus'), { key: 'ArrowDown' })
    expect(useAppStore.getState().dashboardWidgets).toEqual(['quick_launch', 'todays_focus', 'next_meeting'])

    fireEvent.click(screen.getByRole('button', { name: /Customize dashboard/i }))
    const dialog = screen.getByRole('dialog', { name: /Customize dashboard/i })
    const rowIds = within(dialog)
      .getAllByTestId(/^widget-row-(quick_launch|todays_focus|next_meeting)$/)
      .map((el) => el.dataset.testid)
    expect(rowIds).toEqual(['widget-row-quick_launch', 'widget-row-todays_focus', 'widget-row-next_meeting'])
  })
})

// →2924: the Cross-team Blockers widget is gone. It fired an unscoped Jira
// fetch on every Dashboard mount whose backend side effect flooded the task
// list with duplicate [Blocker] tasks and starved Tasks polling.
describe('Blockers widget removal (→2924)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'ToriOS', darkMode: true, showTour: false, dashboardWidgets: [...DEFAULT_DASHBOARD_WIDGETS] })
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

  it('renders no blockers widget and never calls its endpoint', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('widget-blockers-widget')).toBeNull()
    const blockerCalls = mockedApiGet.mock.calls.filter((c) =>
      String(c[0]).includes('/coordination/blockers'),
    )
    expect(blockerCalls).toHaveLength(0)
  })

  it('a saved layout that still names blockers_widget renders without it', async () => {
    // A stale saved layout (localStorage or server) may still carry the
    // removed id. The dashboard must render normally and simply skip it.
    useAppStore.setState({ dashboardWidgets: ['todays_focus', 'blockers_widget', 'quick_launch'] })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('Day Summary')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('widget-blockers-widget')).toBeNull()
    expect(screen.getByTestId('widget-move-handle-quick_launch')).toBeInTheDocument()
  })
})

