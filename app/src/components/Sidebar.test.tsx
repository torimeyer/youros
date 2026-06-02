import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { BrowserRouter, MemoryRouter } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}))

import { api } from '../lib/api'
import { _resetSidebarBus } from '../lib/sidebarBus'
import { useRunningAgentsStore } from '../stores/runningAgents'

const mockedApiGet = vi.mocked(api.get)

const DEFAULT_FEATURES = [
  { label: 'Chat', enabled: true },
  { label: 'Agents', enabled: true },
  { label: 'Activity', enabled: true },
  { label: 'Projects', enabled: true },
  { label: 'Drive', enabled: true },
  { label: 'Calendar', enabled: true },
  { label: 'Gmail', enabled: true },
  { label: 'iMessage', enabled: true },
  { label: 'Slack', enabled: true },
  { label: 'GitHub', enabled: true },
  { label: 'Jira', enabled: true },
  { label: 'Confluence', enabled: true },
  { label: 'Cost Tracking', enabled: true },
  { label: 'Automations', enabled: true },
]

function renderSidebar(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Sidebar />
    </MemoryRouter>
  )
}

// Helper: expand all groups so every nav item is in the DOM.
// Only expands groups that are currently collapsed (items container absent).
function expandAllGroups() {
  for (const id of ['integrations']) {
    if (!screen.queryByTestId(`group-items-${id}`)) {
      const header = screen.queryByTestId(`group-header-${id}`)
      if (header) fireEvent.click(header)
    }
  }
}

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
    })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('renders all navigation items when all features enabled (groups expanded)', async () => {
    renderSidebar()
    // Ensure all groups are expanded (only expand if currently collapsed)
    expandAllGroups()

    const navLabels = ['Home', 'Tasks', 'Specs', 'Kanban view', 'Agents', 'Calendar', 'Gmail', 'Settings']
    for (const label of navLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('Activity is NOT in the sidebar (moved to Settings)', () => {
    renderSidebar()
    expect(screen.queryByText('Activity')).not.toBeInTheDocument()
    const activityLink = document.querySelector('a[href="/activity"]')
    expect(activityLink).toBeNull()
  })

  it('sidebar shows "Usage" label for the costs nav entry', () => {
    renderSidebar()
    expect(screen.getByText('Usage')).toBeInTheDocument()
    expect(screen.queryByText('Cost Tracking')).not.toBeInTheDocument()
  })

  it('Usage nav link points to /costs', () => {
    renderSidebar()
    const link = screen.getByText('Usage').closest('a')
    expect(link).toHaveAttribute('href', '/costs')
  })

  it('renders the OS name from the store', () => {
    renderSidebar()
    expect(screen.getByText('yourOS')).toBeInTheDocument()
  })

  it('renders a custom OS name when store is updated', () => {
    useAppStore.setState({ osName: 'CustomOS' })
    renderSidebar()
    expect(screen.getByText('CustomOS')).toBeInTheDocument()
  })

  it('renders the OS name containing "OS"', () => {
    renderSidebar()
    expect(screen.getByTestId('sidebar-os-name').textContent).toMatch(/OS/)
  })

  it('does not render mode badge (team mode hidden)', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    renderSidebar()
    expect(screen.queryByTestId('sidebar-mode-badge')).not.toBeInTheDocument()
  })

  it('does not render mode badge in team mode either (team mode hidden)', () => {
    useAppStore.setState({ instanceMode: 'team' })
    renderSidebar()
    expect(screen.queryByTestId('sidebar-mode-badge')).not.toBeInTheDocument()
  })

  it('does not show team CTA in personal mode (team mode hidden)', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    renderSidebar()
    expect(screen.queryByTestId('team-cta')).not.toBeInTheDocument()
  })

  it('does not show team CTA in team mode (team mode hidden)', () => {
    useAppStore.setState({ instanceMode: 'team' })
    renderSidebar()
    expect(screen.queryByTestId('team-cta')).not.toBeInTheDocument()
  })

  it('does not show team-setup link (team mode hidden)', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    renderSidebar()
    expect(screen.queryByTestId('team-cta')).not.toBeInTheDocument()
  })

  it("does not show 'Start or join a team' text (team mode hidden)", () => {
    useAppStore.setState({ instanceMode: 'personal' })
    renderSidebar()
    expect(screen.queryByText(/Start or join a team/)).not.toBeInTheDocument()
  })

  it("team CTA text is not visible in team mode", () => {
    useAppStore.setState({ instanceMode: 'team' })
    renderSidebar()
    expect(screen.queryByText(/Start or join a team/)).not.toBeInTheDocument()
  })

  it('all nav items are links with correct paths (groups expanded)', () => {
    renderSidebar()
    // Ensure all groups are expanded (only expand if currently collapsed)
    expandAllGroups()

    const expectedPaths: Record<string, string> = {
      Home: '/',
      Tasks: '/tasks',
      Specs: '/specs',
      Agents: '/agents',
      Calendar: '/calendar',
      Gmail: '/gmail',
      Settings: '/settings',
    }

    for (const [label, path] of Object.entries(expectedPaths)) {
      const link = screen.getByText(label).closest('a')
      expect(link).toHaveAttribute('href', path)
    }
  })

  it('active link gets highlighted class', () => {
    renderSidebar('/')
    const homeLink = screen.getByText('Home').closest('a')
    expect(homeLink?.className).toContain('accent-highlight')
    expect(homeLink?.className).toContain('accent-border')
  })

  it('inactive links have inactive styling', () => {
    renderSidebar('/')
    const kanbanLink = screen.getByText('Kanban view').closest('a')
    expect(kanbanLink?.className).toContain('text-slate-400')
    expect(kanbanLink?.className).not.toContain('accent-highlight')
  })

  it('does not show agent badge when activeAgents is 0', () => {
    // Sidebar reads count from useRunningAgentsStore (WebSocket-fed).
    // Store starts at count=0 (reset in beforeEach).
    renderSidebar()
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('shows agent badge when activeAgents > 0 (only user-spawned running agents)', async () => {
    useRunningAgentsStore.setState({ count: 2, agents: [
      { name: 'agent-1' },
      { name: 'agent-2' },
    ] })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('shows correct count for multiple active agents', async () => {
    useRunningAgentsStore.setState({ count: 5, agents: [
      { name: 'a1' }, { name: 'a2' }, { name: 'a3' }, { name: 'a4' }, { name: 'a5' },
    ] })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument()
    })
  })

  it('badge updates when store count drops (agent cancelled)', async () => {
    useRunningAgentsStore.setState({ count: 3, agents: [
      { name: 'a1' }, { name: 'a2' }, { name: 'a3' },
    ] })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    })
    // Backend pushes a delta after cancellation: count drops to 2.
    useRunningAgentsStore.setState({ count: 2, agents: [
      { name: 'a2' }, { name: 'a3' },
    ] })
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('Sidebar renders a count badge on the Tasks nav when there are open tasks', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 7 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      const tasksLink = screen.getByText('Tasks').closest('a')
      expect(tasksLink?.querySelector('.rounded-full')).not.toBeNull()
      expect(tasksLink?.textContent).toContain('7')
    })

    // Badge styling must match the Agents badge exactly: green pill,
    // pulsing green dot, tiny bold text. The dot is a nested rounded-full
    // element with animate-pulse applied.
    const tasksLink = screen.getByText('Tasks').closest('a')
    const badge = tasksLink?.querySelector('.bg-green-500\\/20')
    expect(badge).not.toBeNull()
    expect(badge?.className).toContain('text-green-400')
    expect(badge?.className).toContain('text-[10px]')
    expect(badge?.className).toContain('font-bold')
    expect(badge?.querySelector('.animate-pulse')).not.toBeNull()
  })

  it('Sidebar does NOT render a badge on Kanban view when counts are 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/counts')
    })

    const kanbanLink = screen.getByText('Kanban view').closest('a')
    expect(kanbanLink?.querySelectorAll('.rounded-full').length).toBe(0)
    expect(kanbanLink?.textContent).toContain('Kanban view')
  })

  it('Tasks badge relies on /tasks/counts so the backend filters closed and shelved tasks', async () => {
    // The backend /tasks/counts endpoint only returns open tasks (closed
    // and shelved are excluded server side). This test asserts the
    // sidebar consumes the filtered count as-is, never recomputing from
    // an unfiltered /tasks list. Passing this guards against future
    // regressions where status filtering drifts out of sync with the
    // Tasks page.
    let countsCalls = 0
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') {
        countsCalls++
        // Backend already filtered to open only: 4 remain out of a
        // hypothetical 10 total (6 closed/shelved excluded).
        return Promise.resolve({ open: 4 })
      }
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      expect(countsCalls).toBeGreaterThanOrEqual(1)
      const tasksLink = screen.getByText('Tasks').closest('a')
      expect(tasksLink?.textContent).toContain('4')
    })

    // The sidebar must never call /tasks directly to compute the badge.
    expect(mockedApiGet).not.toHaveBeenCalledWith('/tasks')
  })

  it('renders Agents nav item even when API calls fail', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderSidebar()

    // Tasks/specs/etc. polls may fail; sidebar must still render Agents nav.
    await waitFor(() => {
      expect(screen.getByText('Agents')).toBeInTheDocument()
    })
    // No badge: store count is 0 (default).
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('shows store count immediately on mount (no fetch needed)', () => {
    useRunningAgentsStore.setState({ count: 1, agents: [{ name: 'my-agent' }] })
    renderSidebar()
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.textContent).toContain('1')
  })

  it('badge updates reactively when store count changes', async () => {
    useRunningAgentsStore.setState({ count: 0, agents: [] })
    renderSidebar()

    const agentsLink = () => screen.getByText('Agents').closest('a')
    expect(agentsLink()?.querySelector('.animate-pulse')).toBeNull()

    // Store receives a push from the WebSocket feed.
    useRunningAgentsStore.setState({ count: 3, agents: [
      { name: 'x1' }, { name: 'x2' }, { name: 'x3' },
    ] })

    await waitFor(() => {
      expect(agentsLink()?.textContent).toContain('3')
    })
  })

  it('polls tasks every 2 seconds so the badge stays up to date', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    const taskCalls = () => mockedApiGet.mock.calls.filter(c => c[0] === '/tasks/counts').length

    await vi.advanceTimersByTimeAsync(0)
    expect(taskCalls()).toBe(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(taskCalls()).toBe(2)

    await vi.advanceTimersByTimeAsync(2000)
    expect(taskCalls()).toBe(3)

    vi.useRealTimers()
  })

  it('badge hides immediately when store count drops to zero', async () => {
    useRunningAgentsStore.setState({ count: 2, agents: [
      { name: 'x1' }, { name: 'x2' },
    ] })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })

    useRunningAgentsStore.setState({ count: 0, agents: [] })

    await waitFor(() => {
      const agentsLink = screen.getByText('Agents').closest('a')
      expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
    })
  })

  it('bumpTasks triggers an immediate tasks refetch without waiting for the poll', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    const { bumpTasks } = await import('../lib/sidebarBus')

    renderSidebar()
    const taskCalls = () => mockedApiGet.mock.calls.filter(c => c[0] === '/tasks/counts').length

    await vi.advanceTimersByTimeAsync(0)
    expect(taskCalls()).toBe(1)

    await vi.advanceTimersByTimeAsync(100)
    bumpTasks()
    await vi.advanceTimersByTimeAsync(0)
    expect(taskCalls()).toBe(2)

    vi.useRealTimers()
  })

  it('sidebar badge counts only user-spawned running agents, not main session', async () => {
    // Backend's _compute_running_snapshot already filters out the main session
    // before pushing to the store. Sidebar just renders store.count.
    useRunningAgentsStore.setState({ count: 2, agents: [
      { name: 'tasks-health-autofix' },
      { name: 'fix-agents-parse-error' },
    ] })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('sidebar badge excludes chat, audit, hook, and subscription agents', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({
        active: ['chat-abc', 'audit-row', 'hook-row', 'sub-row', 'real-agent'],
        agents: [
          { name: 'chat-abc', status: 'running', source: 'chat' },
          { name: 'audit-row', status: 'running', source: 'audit' },
          { name: 'hook-row', status: 'running', source: 'hook' },
          { name: 'sub-row', status: 'running', source: 'claude-code', model: 'claude-code-subscription' },
          { name: 'real-agent', status: 'running', source: 'claude-code' },
        ],
      })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('1')).toBeInTheDocument()
    })
  })

  it('sidebar badge is hidden when only main session is running', () => {
    // Backend sends count=0 when only the main Claude Code session is running.
    // Store stays at 0 (reset in beforeEach).
    renderSidebar()
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('renders the What\'s New button in the sidebar footer', () => {
    renderSidebar()
    expect(screen.getByTestId('whats-new-button')).toBeInTheDocument()
  })

  it('Tour button is NOT in the sidebar (moved to Settings)', () => {
    renderSidebar()
    expect(screen.queryByTestId('tour-button')).not.toBeInTheDocument()
  })

  it('Rules nav link is NOT in the sidebar (moved to Settings)', () => {
    renderSidebar()
    expect(screen.queryByText('Rules')).not.toBeInTheDocument()
    const rulesLink = document.querySelector('a[href="/settings/rules"]')
    expect(rulesLink).toBeNull()
  })

  it('renders the theme toggle in the sidebar footer', () => {
    renderSidebar()
    expect(screen.getByTestId('theme-toggle')).toBeInTheDocument()
  })

  it('theme toggle is located inside the sidebar (bottom area)', () => {
    renderSidebar()
    const toggle = screen.getByTestId('theme-toggle')
    const container = screen.getByTestId('theme-toggle-container')
    expect(container).toContainElement(toggle)
  })

  it('clicking the theme toggle flips darkMode in the store', () => {
    useAppStore.setState({ darkMode: false, toggleDarkMode: () => useAppStore.setState((s) => ({ darkMode: !s.darkMode })) })
    renderSidebar()

    expect(useAppStore.getState().darkMode).toBe(false)

    const toggle = screen.getByTestId('theme-toggle')
    fireEvent.click(toggle)

    expect(useAppStore.getState().darkMode).toBe(true)
  })

  it('pill container has padding class so indicator never touches the outer border', () => {
    renderSidebar()
    const toggle = screen.getByTestId('theme-toggle')
    expect(toggle.className).toContain('p-1')
  })

  it('each icon half has w-1/2 flex items-center justify-center', () => {
    renderSidebar()
    const toggle = screen.getByTestId('theme-toggle')
    const halves = toggle.querySelectorAll('span.w-1\\/2')
    expect(halves.length).toBeGreaterThanOrEqual(2)
    halves.forEach((half) => {
      expect(half.className).toContain('w-1/2')
      expect(half.className).toContain('flex')
      expect(half.className).toContain('items-center')
      expect(half.className).toContain('justify-center')
    })
  })

  it('indicator uses w-1/2 so icons never overlap it', () => {
    renderSidebar()
    const indicator = screen.getByTestId('theme-toggle-indicator')
    expect(indicator.className).toContain('w-[calc(50%-4px)]')
  })

  it('indicator slides to right when darkMode is true', () => {
    useAppStore.setState({ darkMode: true })
    renderSidebar()

    const indicator = screen.getByTestId('theme-toggle-indicator')
    expect(indicator.className).toContain('translate-x-[100%]')
  })

  it('indicator slides to left when darkMode is false', () => {
    useAppStore.setState({ darkMode: false })
    renderSidebar()

    const indicator = screen.getByTestId('theme-toggle-indicator')
    expect(indicator.className).toContain('translate-x-0')
    expect(indicator.className).not.toContain('translate-x-[100%]')
  })

  it('theme toggle container has top margin and divider for spacing from Settings', () => {
    renderSidebar()
    const container = screen.getByTestId('theme-toggle-container')
    expect(container.className).toContain('mt-3')
    expect(container.className).toContain('border-t')
    expect(container.className).toContain('pt-3')
  })

  it('outer button has h-10 class for taller pill', () => {
    renderSidebar()
    const toggle = screen.getByTestId('theme-toggle')
    expect(toggle.className).toContain('h-10')
  })

  it('icons use fontSize 20 to match the taller pill', () => {
    renderSidebar()
    const toggle = screen.getByTestId('theme-toggle')
    const iconSpans = Array.from(toggle.querySelectorAll<HTMLElement>('.material-symbols-outlined'))
    expect(iconSpans.length).toBeGreaterThanOrEqual(2)
    iconSpans.forEach((el) => {
      expect(el.style.fontSize).toBe('20px')
    })
  })

  it('Sidebar renders a count badge on the Specs nav when there are unfinished specs', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 3, total: 5 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      const specsLink = screen.getByText('Specs').closest('a')
      expect(specsLink?.textContent).toContain('3')
    })

    // Styling must match the Agents badge: green pill, pulsing
    // green dot, tiny bold text. Guards against one badge drifting out of
    // visual sync with the others.
    const specsLink = screen.getByText('Specs').closest('a')
    const badge = specsLink?.querySelector('.bg-green-500\\/20')
    expect(badge).not.toBeNull()
    expect(badge?.className).toContain('text-green-400')
    expect(badge?.className).toContain('text-[10px]')
    expect(badge?.className).toContain('font-bold')
    expect(badge?.querySelector('.animate-pulse')).not.toBeNull()
  })

  it('Sidebar does NOT render a badge on Specs nav when unfinished count is 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 4 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs/counts')
    })

    const specsLink = screen.getByText('Specs').closest('a')
    // No count pill should render when unfinished count is 0.
    expect(specsLink?.querySelectorAll('.rounded-full').length).toBe(0)
    expect(specsLink?.textContent).toContain('Specs')
  })

  it('polls specs every 2 seconds so the badge stays up to date', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    const specCalls = () => mockedApiGet.mock.calls.filter(c => c[0] === '/specs/counts').length

    await vi.advanceTimersByTimeAsync(0)
    expect(specCalls()).toBe(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(specCalls()).toBe(2)

    await vi.advanceTimersByTimeAsync(2000)
    expect(specCalls()).toBe(3)

    vi.useRealTimers()
  })

  it('bumpSpecs triggers an immediate specs refetch without waiting for the poll', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    const { bumpSpecs } = await import('../lib/sidebarBus')

    renderSidebar()
    const specCalls = () => mockedApiGet.mock.calls.filter(c => c[0] === '/specs/counts').length

    await vi.advanceTimersByTimeAsync(0)
    expect(specCalls()).toBe(1)

    await vi.advanceTimersByTimeAsync(100)
    bumpSpecs()
    await vi.advanceTimersByTimeAsync(0)
    expect(specCalls()).toBe(2)

    vi.useRealTimers()
  })

  it('three badges (Agents, Tasks, Specs) render correct counts independently', async () => {
    // Agents badge reads from Zustand store (WebSocket-fed).
    // Tasks badge shows openTasksCount. Specs badge shows unfinishedSpecs.
    useRunningAgentsStore.setState({ count: 1, agents: [{ name: 'a1' }] })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 2 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 3, total: 3 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      const agentsLink = screen.getByText('Agents').closest('a')
      expect(agentsLink?.textContent).toContain('1')
      const tasksLink = screen.getByText('Tasks').closest('a')
      expect(tasksLink?.textContent).toContain('2')
      const specsLink = screen.getByText('Specs').closest('a')
      expect(specsLink?.textContent).toContain('3')
    })
  })

  it('badge count updates when store.count changes', async () => {
    useRunningAgentsStore.setState({ count: 0, agents: [] })
    renderSidebar()

    expect(screen.queryByText('3')).not.toBeInTheDocument()

    useRunningAgentsStore.setState({ count: 3, agents: [
      { name: 'a1' }, { name: 'a2' }, { name: 'a3' },
    ] })

    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    })
  })
})

// ------------- Grouped nav tests -------------

describe('Sidebar grouped nav', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
    })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('Integrations group header is rendered', () => {
    renderSidebar()
    expect(screen.getByTestId('group-header-integrations')).toBeInTheDocument()
  })

  it('collapsed Integrations group shows only its header, not sub-items', () => {
    // Default state: no saved collapsed state, groups start in default (not collapsed = open)
    // Force collapsed via localStorage before render
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    renderSidebar()

    // Header should be visible
    expect(screen.getByTestId('group-header-integrations')).toBeInTheDocument()
    // Items container should NOT be in the DOM
    expect(screen.queryByTestId('group-items-integrations')).not.toBeInTheDocument()
    // Gmail link should not appear
    expect(screen.queryByText('Gmail')).not.toBeInTheDocument()
  })

  it('expanded Integrations group shows all sub-items including Jira and Confluence', () => {
    renderSidebar()
    // Ensure integrations is expanded (click header to expand if collapsed)
    const header = screen.getByTestId('group-header-integrations')
    // If items not present, expand
    if (!screen.queryByTestId('group-items-integrations')) {
      fireEvent.click(header)
    }
    const items = screen.getByTestId('group-items-integrations')
    expect(items).toBeInTheDocument()
    expect(screen.getByText('Gems')).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
    expect(screen.getByText('Calendar')).toBeInTheDocument()
    expect(screen.getByText('Messages')).toBeInTheDocument()
    expect(screen.getByText('Slack')).toBeInTheDocument()
    expect(screen.getByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('Jira')).toBeInTheDocument()
    expect(screen.getByText('Confluence')).toBeInTheDocument()
  })

  it('clicking a collapsed group header expands it and shows sub-items', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    renderSidebar()

    // Start collapsed
    expect(screen.queryByTestId('group-items-integrations')).not.toBeInTheDocument()

    // Click header to expand
    fireEvent.click(screen.getByTestId('group-header-integrations'))

    // Items should now be visible
    expect(screen.getByTestId('group-items-integrations')).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
  })

  it('clicking an expanded group header collapses it and hides sub-items', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-integrations')

    // Ensure expanded first
    if (!screen.queryByTestId('group-items-integrations')) {
      fireEvent.click(header)
    }
    expect(screen.getByTestId('group-items-integrations')).toBeInTheDocument()

    // Click to collapse
    fireEvent.click(header)
    expect(screen.queryByTestId('group-items-integrations')).not.toBeInTheDocument()
  })

  it('toggle persists collapsed state to localStorage', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-integrations')

    // Expand first if needed
    if (!screen.queryByTestId('group-items-integrations')) {
      fireEvent.click(header)
    }

    // Collapse
    fireEvent.click(header)

    const saved = JSON.parse(localStorage.getItem('sidebar-group-collapsed') ?? '{}')
    expect(saved.integrations).toBe(true)
  })

  it('toggle persists expanded state (false) to localStorage', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    renderSidebar()

    // Click to expand
    fireEvent.click(screen.getByTestId('group-header-integrations'))

    const saved = JSON.parse(localStorage.getItem('sidebar-group-collapsed') ?? '{}')
    expect(saved.integrations).toBe(false)
  })

  it('active route group (Integrations) auto-expands on mount when collapsed in localStorage', () => {
    // Store integrations as collapsed
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    // Render with a route that belongs to integrations
    renderSidebar('/gmail')

    // Group items must be visible because /gmail belongs to integrations
    expect(screen.getByTestId('group-items-integrations')).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
  })

  it('Integrations group stays collapsed when navigating to a non-integrations route', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    // Render at a top-level route (Home)
    renderSidebar('/')

    expect(screen.queryByTestId('group-items-integrations')).not.toBeInTheDocument()
  })

  it('every route is reachable: all links present when all groups expanded', () => {
    renderSidebar()
    // Ensure all groups are expanded (only expand if currently collapsed)
    expandAllGroups()

    const expectedLinks = [
      { label: 'Home', href: '/' },
      { label: 'Tasks', href: '/tasks' },
      { label: 'Specs', href: '/specs' },
      { label: 'Agents', href: '/agents' },
      { label: 'Gmail', href: '/gmail' },
      { label: 'Calendar', href: '/calendar' },
      { label: 'Messages', href: '/imessage' },
      { label: 'Slack', href: '/slack' },
      { label: 'GitHub', href: '/github' },
      { label: 'Usage', href: '/costs' },
      { label: 'Settings', href: '/settings' },
    ]

    for (const { label, href } of expectedLinks) {
      const link = screen.getByText(label).closest('a')
      expect(link, `${label} link missing`).not.toBeNull()
      expect(link).toHaveAttribute('href', href)
    }

    // Automations is no longer in the sidebar.
    expect(screen.queryByText('Automations')).not.toBeInTheDocument()
  })

  it('one group header rendered: integrations (files/comms/automation groups removed)', () => {
    renderSidebar()
    expect(screen.getByTestId('group-header-integrations')).toBeInTheDocument()
    expect(screen.queryByTestId('group-header-files')).not.toBeInTheDocument()
    expect(screen.queryByTestId('group-header-comms')).not.toBeInTheDocument()
    expect(screen.queryByTestId('group-header-automation')).not.toBeInTheDocument()
  })

  it('sidebar does not show an Automations top-level entry', () => {
    renderSidebar()
    // Automations has been removed from the sidebar. The Workflows route
    // and backend service are still there for internal use, but the entry
    // point is no longer surfaced in navigation.
    expect(screen.queryByText('Automations')).not.toBeInTheDocument()
    const workflowsLink = document.querySelector('a[href="/workflows"]')
    expect(workflowsLink).toBeNull()
  })

  it('test_sidebar_no_automation_group_header', () => {
    renderSidebar()
    expect(screen.queryByTestId('group-header-automation')).not.toBeInTheDocument()
    expect(screen.queryByTestId('group-items-automation')).not.toBeInTheDocument()
    // The literal "AUTOMATION" group title must not appear anywhere
    expect(screen.queryByText(/^Automation$/)).not.toBeInTheDocument()
  })

  it('test_sidebar_usage_in_settings_or_bottom', () => {
    renderSidebar()
    // Usage is now a standalone bottom nav item (near Settings), not inside any group
    const usageLink = screen.getByTestId('usage-nav-link')
    const settingsLink = screen.getByText('Settings').closest('a')
    expect(usageLink).toHaveAttribute('href', '/costs')
    expect(settingsLink).not.toBeNull()
    // Usage must appear before Settings in DOM order (both in bottom area)
    const position = usageLink.compareDocumentPosition(settingsLink!)
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('Integrations group header shows rolled-up gmail badge when collapsed and gmail unread > 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/gmail/auth/status') return Promise.resolve({ authenticated: true, unread_count: 5 })
      return Promise.resolve({})
    })
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    renderSidebar()

    // Wait for gmail badge to load
    await waitFor(() => {
      const header = screen.getByTestId('group-header-integrations')
      // Badge should appear inside the header
      expect(header.textContent).toContain('5')
    })
  })

  it('ostk entry is hidden when power user mode is off', () => {
    useAppStore.setState({ powerUserMode: false })
    renderSidebar()
    // Integrations group is expanded by default; ostk entry must NOT appear
    expect(screen.queryByText(/^ostk$/)).not.toBeInTheDocument()
  })

  it('ostk entry is shown when power user mode is on', () => {
    useAppStore.setState({ powerUserMode: true })
    renderSidebar()
    expect(screen.getByText(/^ostk$/)).toBeInTheDocument()
  })

  // --- new tests for →1506 ---

  it('Integrations group header has aria-expanded=true when expanded', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-integrations')
    if (!screen.queryByTestId('group-items-integrations')) {
      fireEvent.click(header)
    }
    expect(header).toHaveAttribute('aria-expanded', 'true')
  })

  it('Integrations group header has aria-expanded=false when collapsed', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ integrations: true }))
    renderSidebar()
    const header = screen.getByTestId('group-header-integrations')
    expect(header).toHaveAttribute('aria-expanded', 'false')
  })

  it('Integrations group header has aria-controls linking to items container', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-integrations')
    expect(header).toHaveAttribute('aria-controls', 'group-items-integrations')
  })

  it('Docs/Drive/Gmail/Gems are NOT direct children of primary-nav (only under Integrations)', () => {
    renderSidebar()
    const primaryNav = screen.getByTestId('primary-nav')
    const directLinks = Array.from(primaryNav.querySelectorAll(':scope > a'))
    const directHrefs = directLinks.map((l) => l.getAttribute('href'))
    expect(directHrefs).not.toContain('/files')
    expect(directHrefs).not.toContain('/drive')
    expect(directHrefs).not.toContain('/gmail')
    expect(directHrefs).not.toContain('/gems')
  })
})

// ------------- Regression guard for needle 293 (unchanged) -------------

describe('Sidebar health dot debouncing (needle 293)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
    })
  })

  const backendDot = () => screen.getByTestId('backend-status-dot') as HTMLElement

  const isRed = (el: HTMLElement) => el.className.includes('bg-red-400')
  const isGreen = (el: HTMLElement) => el.className.includes('bg-green-400')
  const isAmber = (el: HTMLElement) => el.className.includes('bg-amber-400')

  const collectDotStates = async (steps: Array<() => Promise<void>>): Promise<string[]> => {
    const states: string[] = []
    states.push(backendDot().className)
    for (const step of steps) {
      await step()
      states.push(backendDot().className)
    }
    return states
  }

  it('stays green through a single failed poll then success (fast restart case)', async () => {
    vi.useFakeTimers()
    let clockCalls = 0
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        clockCalls++
        if (clockCalls === 1) return { kernel: 'v2.5.0' }
        if (clockCalls === 2) throw new Error('Upstream unavailable')
        return { kernel: 'v2.5.0' }
      }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    render(<MemoryRouter><Sidebar /></MemoryRouter>)

    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isGreen(backendDot())).toBe(true)

    const states = await collectDotStates([
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      async () => { await vi.advanceTimersByTimeAsync(2_000) },
    ])

    for (const className of states) {
      expect(className).not.toContain('bg-red-400')
    }
    expect(isGreen(backendDot())).toBe(true)
    expect(clockCalls).toBe(3)

    vi.useRealTimers()
  })

  it('shows starting state then red when backend never responds (no prior healthy connection)', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        throw new Error('ECONNREFUSED')
      }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    render(<MemoryRouter><Sidebar /></MemoryRouter>)

    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isRed(backendDot())).toBe(false)

    // After 2 consecutive failures (~4s): 'starting' (calm slate), not red or amber.
    // Never connected = backend warming up, not a genuine outage.
    await vi.advanceTimersByTimeAsync(4_000)
    expect(backendDot().className).toContain('bg-slate-400')
    expect(isAmber(backendDot())).toBe(false)
    expect(isRed(backendDot())).toBe(false)

    // After 7 consecutive failures (~14s): red regardless
    await vi.advanceTimersByTimeAsync(10_000)
    expect(isRed(backendDot())).toBe(true)

    vi.useRealTimers()
  })

  it('stays green across fail then success then fail (flap case)', async () => {
    vi.useFakeTimers()
    let clockCalls = 0
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        clockCalls++
        if (clockCalls === 1) return { kernel: 'v2.5.0' }
        if (clockCalls === 2) throw new Error('Upstream unavailable')
        if (clockCalls === 3) return { kernel: 'v2.5.0' }
        if (clockCalls === 4) throw new Error('Upstream unavailable')
        return { kernel: 'v2.5.0' }
      }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    render(<MemoryRouter><Sidebar /></MemoryRouter>)

    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isGreen(backendDot())).toBe(true)

    const states = await collectDotStates([
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      async () => { await vi.advanceTimersByTimeAsync(2_000) },
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      async () => { await vi.advanceTimersByTimeAsync(2_000) },
    ])

    for (const className of states) {
      expect(className).not.toContain('bg-red-400')
    }
    expect(isGreen(backendDot())).toBe(true)
    expect(clockCalls).toBe(5)

    vi.useRealTimers()
  })
})

describe('Sidebar backend status dot (→1229)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
    useAppStore.setState({ osName: 'yourOS', features: DEFAULT_FEATURES })
  })

  it('dot starts green when /api/status/clock returns 200', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') return { kernel: 'v2.5.0' }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(1)
    const dot = screen.getByTestId('backend-status-dot')
    expect(dot.className).toContain('bg-green-400')
    vi.useRealTimers()
  })

  it('dot shows starting state (calm gray pulse, not amber) when never connected after 2 failures', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') throw new Error('ECONNREFUSED')
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(4_000)
    const dot = screen.getByTestId('backend-status-dot')
    // 'starting' uses calm slate pulse, NOT alarming amber
    expect(dot.className).toContain('bg-slate-400')
    expect(dot.className).toContain('animate-pulse')
    expect(dot.className).not.toContain('bg-amber-400')
    expect(dot.className).not.toContain('bg-red-400')
    vi.useRealTimers()
  })

  it('dot becomes amber after 2 failures when previously healthy (genuine problem)', async () => {
    vi.useFakeTimers()
    let healthy = true
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        if (healthy) return { kernel: 'v2.5.0' }
        throw new Error('ECONNREFUSED')
      }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    // Let first successful poll fire so hasEverBeenHealthy becomes true
    // Two advances needed: first triggers useEffect/checkHealth, second flushes the async continuation
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(screen.getByTestId('backend-status-dot').className).toContain('bg-green-400')
    // Now start failing — next poll is at SUCCESS_INTERVAL (15s), then FAILURE_INTERVAL (2s) each
    // Need 2 failures to hit AMBER_THRESHOLD: 15_000ms for first + 2_000ms for second
    healthy = false
    // First failure poll at t=15s, then second at t=17s. Split the advance and
    // flush microtasks between so the rejected-promise continuation runs.
    await vi.advanceTimersByTimeAsync(15_100)
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(2_100)
    await vi.advanceTimersByTimeAsync(1)
    const dot = screen.getByTestId('backend-status-dot')
    expect(dot.className).toContain('bg-amber-400')
    expect(dot.className).not.toContain('bg-red-400')
    vi.useRealTimers()
  })

  it('dot becomes red after 7 consecutive failures (~14s)', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') throw new Error('ECONNREFUSED')
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(14_000)
    const dot = screen.getByTestId('backend-status-dot')
    expect(dot.className).toContain('bg-red-400')
    vi.useRealTimers()
  })

  it('dot returns to green after recovery', async () => {
    vi.useFakeTimers()
    let calls = 0
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        calls++
        if (calls <= 8) throw new Error('ECONNREFUSED')
        return { kernel: 'v2.5.0' }
      }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(14_000)
    expect(screen.getByTestId('backend-status-dot').className).toContain('bg-red-400')
    await vi.advanceTimersByTimeAsync(2_000)
    await vi.advanceTimersByTimeAsync(1)
    expect(screen.getByTestId('backend-status-dot').className).toContain('bg-green-400')
    vi.useRealTimers()
  })

  it('exactly one backend-status-dot element rendered', () => {
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    const dots = screen.getAllByTestId('backend-status-dot')
    expect(dots).toHaveLength(1)
  })

  it('tooltip contains the kernel name when present', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') return { kernel: 'v4.0.0' }
      if (url.startsWith('/agents')) return { active: [], agents: [] }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(1)
    const dot = screen.getByTestId('backend-status-dot')
    expect(dot.title).toContain('v4.0.0')
    vi.useRealTimers()
  })
})

describe('Sidebar status panel does not expose Claude indicator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
      defaultChatModel: 'claude',
    })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      if (url === '/sessions/active') return Promise.resolve({ active_count: 0 })
      if (url === '/status/clock') return Promise.resolve({ kernel: 'v4.0.0' })
      if (url === '/upgrade/status') return Promise.resolve({ myos: { current: 'v4.0.0' } })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('shows single Backend status dot but not sessions and not a Claude row', async () => {
    renderSidebar()

    // Single backend status dot is rendered.
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    expect(screen.getByTestId('backend-status-dot')).toBeTruthy()

    // System row is gone — kernel name is in the tooltip, not a separate row.
    expect(screen.queryByText((_content, node) => {
      return !!node && node.tagName === 'SPAN' && (node.textContent ?? '').startsWith('System')
    })).toBeNull()

    // Sessions row is only rendered when count > 0. When active_count is
    // zero the row is omitted entirely (no "No sessions" text).
    expect(screen.queryByText('No sessions')).toBeNull()
    expect(screen.queryByText(/session/i)).toBeNull()

    // Claude row must not be present. Even though the configured chat
    // model is "claude", the status panel must stay provider-generic.
    expect(screen.queryByText('Claude')).toBeNull()
    expect(screen.queryByText('LLM')).toBeNull()
    expect(screen.queryByText('Gemini')).toBeNull()
  })

  it('does not call /settings/probe on mount', async () => {
    renderSidebar()
    // Give effects a chance to run.
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    const probeCalls = mockedApiGet.mock.calls.filter(
      ([url]) => typeof url === 'string' && url.startsWith('/settings/probe')
    )
    expect(probeCalls).toHaveLength(0)
  })
})

describe('Sidebar status panel never shows a sessions count (regression)', () => {
  // Earlier regressions surfaced a "14 sessions" row in the sidebar
  // status panel even after sessions were removed from the Tasks page.
  // The user expects sessions to be gone from this surface entirely.
  // These tests lock that in by asserting no "N session(s)" text ever
  // renders in the status panel, across realistic backend payloads.
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
    })
  })

  function mockSessions(payload: Record<string, unknown>) {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      if (url === '/sessions/active') return Promise.resolve(payload)
      if (url === '/status/clock') return Promise.resolve({ kernel: 'v4.0.0' })
      if (url === '/upgrade/status') return Promise.resolve({ myos: { current: 'v4.0.0' } })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  }

  it('does not render any "N sessions" line when backend reports one session', async () => {
    mockSessions({ count: 1, active_count: 0, idle_count: 1 })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    expect(screen.queryByText(/\d+ sessions?/i)).toBeNull()
    expect(screen.queryByText(/session/i)).toBeNull()
  })

  it('does not render "14 sessions" when backend reports fourteen sessions', async () => {
    // Direct regression for the user-reported screenshot of a sidebar
    // showing "Backend / System v4.0.0 / 14 sessions".
    mockSessions({ count: 14, active_count: 14, idle_count: 0 })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    expect(screen.queryByText('14 sessions')).toBeNull()
    expect(screen.queryByText(/\d+ sessions?/i)).toBeNull()
  })

  it('does not render any sessions text when backend reports zero', async () => {
    mockSessions({ count: 0, active_count: 0, idle_count: 0 })
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    expect(screen.queryByText(/session/i)).toBeNull()
  })
})

describe('Jira and Confluence sidebar entries', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({ osName: 'yourOS', features: DEFAULT_FEATURES })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('Jira link renders in Integrations group', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.getByText('Jira')).toBeInTheDocument()
  })

  it('Confluence link renders in Integrations group', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.getByText('Confluence')).toBeInTheDocument()
  })

  it('Jira link points to /jira', () => {
    renderSidebar()
    expandAllGroups()
    const link = screen.getByText('Jira').closest('a')
    expect(link).toHaveAttribute('href', '/jira')
  })

  it('Confluence link points to /confluence', () => {
    renderSidebar()
    expandAllGroups()
    const link = screen.getByText('Confluence').closest('a')
    expect(link).toHaveAttribute('href', '/confluence')
  })
})

describe('Files and Drive sidebar entries', () => {
  it('shows Docs entry in Integrations group (renamed from Files)', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.getByText('Docs')).toBeInTheDocument()
    expect(screen.queryByText('Files')).not.toBeInTheDocument()
  })

  it('shows Drive entry in Files & Docs group', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.getByText('Drive')).toBeInTheDocument()
  })

  it('Files entry links to /files', () => {
    renderSidebar()
    expandAllGroups()
    const links = screen.getAllByRole('link')
    expect(links.some((l) => l.getAttribute('href') === '/files')).toBe(true)
  })

  it('Drive entry links to /drive', () => {
    renderSidebar()
    expandAllGroups()
    const links = screen.getAllByRole('link')
    expect(links.some((l) => l.getAttribute('href') === '/drive')).toBe(true)
  })

  it('does not render Inbox in the nav', () => {
    renderSidebar()
    expandAllGroups()
    const links = screen.getAllByRole('link')
    expect(links.some((l) => l.getAttribute('href') === '/inbox')).toBe(false)
  })
})

describe('Sidebar — nav restructure (→1489)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
    useAppStore.setState({
      osName: 'yourOS',
      features: DEFAULT_FEATURES,
    })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('renders Tasks as a standalone nav item linking to /tasks', () => {
    renderSidebar()
    expandAllGroups()
    const links = screen.getAllByRole('link')
    expect(links.some((l) => l.getAttribute('href') === '/tasks')).toBe(true)
  })

  it('renders Specs as a standalone nav item linking to /specs', () => {
    renderSidebar()
    expandAllGroups()
    const links = screen.getAllByRole('link')
    expect(links.some((l) => l.getAttribute('href') === '/specs')).toBe(true)
  })

  it('Tasks badge shows open task count', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 7 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    expandAllGroups()
    await waitFor(() => {
      const tasksLink = screen.getByText('Tasks').closest('a')
      expect(tasksLink?.textContent).toContain('7')
    })
  })

  it('Specs badge shows unfinished specs count', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 3, total: 10 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    expandAllGroups()
    await waitFor(() => {
      const specsLink = screen.getByText('Specs').closest('a')
      expect(specsLink?.textContent).toContain('3')
    })
  })
})

describe('nav rename and reorder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
    useAppStore.setState({ osName: 'yourOS', features: DEFAULT_FEATURES })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('Files label is renamed to Docs', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.queryByText('Files')).not.toBeInTheDocument()
    expect(screen.getByText('Docs')).toBeInTheDocument()
  })

  it('Docs nav link points to /files route', () => {
    renderSidebar()
    expandAllGroups()
    const link = screen.getByText('Docs').closest('a')
    expect(link).toHaveAttribute('href', '/files')
  })

  it('"My Gems" label is not in the sidebar', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.queryByText('My Gems')).not.toBeInTheDocument()
  })

  it('Gems is inside the Integrations group linking to /gems', () => {
    renderSidebar()
    expandAllGroups()
    const link = screen.getByText('Gems').closest('a')
    expect(link).toHaveAttribute('href', '/gems')
    const integrationsGroup = screen.getByTestId('group-items-integrations')
    expect(integrationsGroup.contains(link)).toBe(true)
  })

  it('Gems is NOT a direct top-level nav item (lives inside Integrations)', () => {
    renderSidebar()
    const primaryNav = screen.getByTestId('primary-nav')
    const directLinks = Array.from(primaryNav.querySelectorAll(':scope > a'))
    expect(directLinks.some((l) => l.getAttribute('href') === '/gems')).toBe(false)
  })

  it('Home is the first link in the primary nav', () => {
    renderSidebar()
    const primaryNav = screen.getByTestId('primary-nav')
    const directLinks = primaryNav.querySelectorAll(':scope > a')
    expect(directLinks[0]?.getAttribute('href')).toBe('/')
  })

  it('Tasks and Specs appear before Agents in the nav order', () => {
    renderSidebar()
    const primaryNav = screen.getByTestId('primary-nav')
    const allLinks = Array.from(primaryNav.querySelectorAll('a'))
    const tasksIdx = allLinks.findIndex((l) => l.getAttribute('href') === '/tasks')
    const specsIdx = allLinks.findIndex((l) => l.getAttribute('href') === '/specs')
    const agentsIdx = allLinks.findIndex((l) => l.getAttribute('href') === '/agents')
    expect(tasksIdx).toBeGreaterThan(-1)
    expect(specsIdx).toBeGreaterThan(-1)
    expect(agentsIdx).toBeGreaterThan(-1)
    expect(tasksIdx).toBeLessThan(agentsIdx)
    expect(specsIdx).toBeLessThan(agentsIdx)
  })
})
