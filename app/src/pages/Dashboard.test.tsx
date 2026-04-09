import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

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
      expect(screen.getByText('No activity to summarize yet.')).toBeInTheDocument()
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
})

