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

const mockedApiGet = vi.mocked(api.get)

const DEFAULT_FEATURES = [
  { label: 'Chat', enabled: true },
  { label: 'Tasks', enabled: true },
  { label: 'Agents', enabled: true },
  { label: 'Activity', enabled: true },
  { label: 'Projects', enabled: true },
  { label: 'Drive', enabled: true },
  { label: 'Calendar', enabled: true },
  { label: 'Gmail', enabled: true },
  { label: 'iMessage', enabled: true },
  { label: 'Slack', enabled: true },
  { label: 'GitHub', enabled: true },
  { label: 'Cost Tracking', enabled: true },
  { label: 'Specs', enabled: true },
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
  for (const id of ['files', 'comms']) {
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
    useAppStore.setState({
      osName: 'myOS',
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

    const navLabels = ['Home', 'Tasks', 'Agents', 'Calendar', 'Gmail', 'Settings']
    for (const label of navLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('Activity does not render in the sidebar by default', () => {
    // Activity was moved out of the primary nav into the Settings page's
    // Developer section. It must not appear in the sidebar even when all
    // groups are expanded and every feature flag is on.
    renderSidebar()
    expandAllGroups()
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
    expect(screen.getByText('myOS')).toBeInTheDocument()
  })

  it('renders a custom OS name when store is updated', () => {
    useAppStore.setState({ osName: 'CustomOS' })
    renderSidebar()
    expect(screen.getByText('CustomOS')).toBeInTheDocument()
  })

  it('renders the OS name containing "OS"', () => {
    renderSidebar()
    expect(screen.getByText(/OS/)).toBeInTheDocument()
  })

  it('renders Personal badge in personal mode', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    renderSidebar()
    const badge = screen.getByTestId('sidebar-mode-badge')
    expect(badge).toBeInTheDocument()
    expect(badge.textContent).toBe('Personal')
  })

  it('renders Team badge in team mode', () => {
    useAppStore.setState({ instanceMode: 'team' })
    renderSidebar()
    const badge = screen.getByTestId('sidebar-mode-badge')
    expect(badge).toBeInTheDocument()
    expect(badge.textContent).toBe('Team')
  })

  it('all nav items are links with correct paths (groups expanded)', () => {
    renderSidebar()
    // Ensure all groups are expanded (only expand if currently collapsed)
    expandAllGroups()

    const expectedPaths: Record<string, string> = {
      Home: '/',
      Tasks: '/tasks',
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
    const tasksLink = screen.getByText('Tasks').closest('a')
    expect(tasksLink?.className).toContain('text-slate-400')
    expect(tasksLink?.className).not.toContain('accent-highlight')
  })

  it('does not show agent badge when activeAgents is 0', async () => {
    mockedApiGet.mockResolvedValue({ agents: [] })
    renderSidebar()

    // Sidebar badge now hits the compact summary endpoint so it can
    // share a server-filtered set with the Agents page.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(
        '/agents?summary=1&status=running&limit=20'
      )
    })

    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('shows agent badge when activeAgents > 0 (only user-spawned running agents)', async () => {
    // Summary endpoint is already filtered server-side to running +
    // source=claude-code, so the sidebar just has to count the rows it
    // gets back (minus locally dismissed and non-user-spawned rows).
    mockedApiGet.mockResolvedValue({
      agents: [
        { name: 'agent-1', status: 'running', source: 'claude-code' },
        { name: 'agent-2', status: 'running', source: 'claude-code' },
      ],
    })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('shows correct count for multiple active agents', async () => {
    // Summary endpoint returns only running + source=claude-code rows
    // so the sidebar badge is the length of the server response.
    mockedApiGet.mockResolvedValue({
      agents: [
        { name: 'a1', status: 'running', source: 'claude-code' },
        { name: 'a2', status: 'running', source: 'claude-code' },
        { name: 'a3', status: 'running', source: 'claude-code' },
        { name: 'a4', status: 'running', source: 'claude-code' },
        { name: 'a5', status: 'running', source: 'claude-code' },
      ],
    })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument()
    })
  })

  it('excludes locally dismissed agents from the badge count', async () => {
    // Start with three running agents. The Agents page dismisses one of
    // them (for example the user cancelled it), so the sidebar badge
    // should drop to 2 on the next refetch even though the backend still
    // returns all three.
    mockedApiGet.mockResolvedValue({
      agents: [
        { name: 'a1', status: 'running', source: 'claude-code' },
        { name: 'a2', status: 'running', source: 'claude-code' },
        { name: 'a3', status: 'running', source: 'claude-code' },
      ],
    })
    const { addDismissed, bumpAgents } = await import('../lib/sidebarBus')
    renderSidebar()
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    })
    // Simulate the Agents page cancelling a1. Nudge the bus so the sidebar
    // refetches right away instead of waiting for its 2s poll tick.
    addDismissed('a1')
    bumpAgents()
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    }, { timeout: 3000 })
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

  it('Sidebar does NOT render a badge on Tasks when count is 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/counts')
    })

    const tasksLink = screen.getByText('Tasks').closest('a')
    expect(tasksLink?.querySelectorAll('.rounded-full').length).toBe(0)
    // Tasks link must contain the label "Tasks" but no numeric count.
    expect(tasksLink?.textContent).toContain('Tasks')
    expect(tasksLink?.textContent).not.toMatch(/\d/)
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

  it('handles API error for agents gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(expect.stringMatching(/^\/agents/))
    })

    expect(screen.getByText('Agents')).toBeInTheDocument()
  })

  it('fetches agents on mount', async () => {
    mockedApiGet.mockResolvedValue({ active: [] })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(expect.stringMatching(/^\/agents/))
    })
  })

  it('polls agents every 2 seconds so the badge stays up to date', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [] })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    const agentCalls = () => mockedApiGet.mock.calls.filter(c => typeof c[0] === 'string' && c[0].startsWith('/agents')).length

    await vi.advanceTimersByTimeAsync(0)
    expect(agentCalls()).toBe(1)

    // Background poll runs at 2 s.
    await vi.advanceTimersByTimeAsync(2000)
    expect(agentCalls()).toBe(2)

    await vi.advanceTimersByTimeAsync(2000)
    expect(agentCalls()).toBe(3)

    vi.useRealTimers()
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

  it('bumpAgents triggers an immediate agents refetch without waiting for the poll', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    const { bumpAgents } = await import('../lib/sidebarBus')

    renderSidebar()
    const agentCalls = () => mockedApiGet.mock.calls.filter(c => typeof c[0] === 'string' && c[0].startsWith('/agents')).length

    await vi.advanceTimersByTimeAsync(0)
    expect(agentCalls()).toBe(1)

    // Only advance 100 ms, well under the 2 s poll. A bump should refetch
    // anyway.
    await vi.advanceTimersByTimeAsync(100)
    bumpAgents()
    await vi.advanceTimersByTimeAsync(0)
    expect(agentCalls()).toBe(2)

    vi.useRealTimers()
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
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({
        active: ['claude-code-31808c4b-5', 'tasks-health-autofix', 'fix-agents-parse-error'],
        agents: [
          { name: 'claude-code-31808c4b-5', status: 'running', source: 'claude-code', description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)' },
          { name: 'tasks-health-autofix', status: 'running', source: 'claude-code' },
          { name: 'fix-agents-parse-error', status: 'running', source: 'claude-code' },
        ],
      })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
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

  it('sidebar badge is hidden when only main session is running', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({
        active: ['claude-code-31808c4b-5'],
        agents: [
          { name: 'claude-code-31808c4b-5', status: 'running', source: 'claude-code', description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)' },
        ],
      })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(expect.stringMatching(/^\/agents/))
    })
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('renders the What\'s New button in the sidebar footer', () => {
    renderSidebar()
    expect(screen.getByTestId('whats-new-button')).toBeInTheDocument()
  })

  it('renders the What\'s New button before the Tour button in DOM order', () => {
    renderSidebar()
    const whatsNewButton = screen.getByTestId('whats-new-button')
    const tourButton = screen.getByTestId('tour-button')

    const position = whatsNewButton.compareDocumentPosition(tourButton)
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
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
    // Specs are nested inside the Files & Docs group, so expand it first.
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 3, total: 5 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    expandAllGroups()

    await waitFor(() => {
      const specsLink = screen.getByText('Specs').closest('a')
      expect(specsLink?.textContent).toContain('3')
    })

    // Styling must match the Agents and Tasks badges: green pill, pulsing
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

  it('Sidebar does NOT render a badge on Specs when unfinished count is 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 4 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    expandAllGroups()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs/counts')
    })

    const specsLink = screen.getByText('Specs').closest('a')
    // No count pill should render when there's nothing unfinished.
    expect(specsLink?.querySelectorAll('.rounded-full').length).toBe(0)
    expect(specsLink?.textContent).toContain('Specs')
    expect(specsLink?.textContent).not.toMatch(/\d/)
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

  it('three badges (Agents, Tasks, Specs) all render correct counts from their own endpoints', async () => {
    // This is the deep-dive check: all three badges wired up, counts from
    // three separate endpoints, no crosstalk. If one endpoint fails or is
    // miswired, only that badge is affected.
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({
        agents: [{ name: 'a1', status: 'running', source: 'claude-code' }],
      })
      if (url === '/tasks/counts') return Promise.resolve({ open: 2 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 3, total: 3 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()
    expandAllGroups()

    await waitFor(() => {
      const agentsLink = screen.getByText('Agents').closest('a')
      expect(agentsLink?.textContent).toContain('1')
      const tasksLink = screen.getByText('Tasks').closest('a')
      expect(tasksLink?.textContent).toContain('2')
      const specsLink = screen.getByText('Specs').closest('a')
      expect(specsLink?.textContent).toContain('3')
    })
  })

    it('updates badge count when API response changes between polls', async () => {
    let agentCallCount = 0
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url.startsWith('/agents')) {
        agentCallCount++
        if (agentCallCount === 1) return { active: [], agents: [] }
        return {
          active: ['a1', 'a2', 'a3'],
          agents: [
            { name: 'a1', status: 'running', source: 'claude-code' },
            { name: 'a2', status: 'running', source: 'claude-code' },
            { name: 'a3', status: 'running', source: 'api' },
          ],
        }
      }
      return { authenticated: false, unread_count: 0 }
    })

    renderSidebar()

    await waitFor(() => {
      expect(agentCallCount).toBeGreaterThanOrEqual(1)
    })
    expect(screen.queryByText('3')).not.toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    }, { timeout: 10000 })
  }, 15000)
})

// ------------- Grouped nav tests -------------

describe('Sidebar grouped nav', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'myOS',
      features: DEFAULT_FEATURES,
    })
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/specs/counts') return Promise.resolve({ unfinished: 0, total: 0 })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
  })

  it('Comms group header is rendered', () => {
    renderSidebar()
    expect(screen.getByTestId('group-header-comms')).toBeInTheDocument()
  })

  it('collapsed Comms group shows only its header, not sub-items', () => {
    // Default state: no saved collapsed state, groups start in default (not collapsed = open)
    // Force collapsed via localStorage before render
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ comms: true }))
    renderSidebar()

    // Header should be visible
    expect(screen.getByTestId('group-header-comms')).toBeInTheDocument()
    // Items container should NOT be in the DOM
    expect(screen.queryByTestId('group-items-comms')).not.toBeInTheDocument()
    // Gmail link should not appear
    expect(screen.queryByText('Gmail')).not.toBeInTheDocument()
  })

  it('expanded Comms group shows all 5 sub-items', () => {
    renderSidebar()
    // Ensure comms is expanded (click header to expand if collapsed)
    const header = screen.getByTestId('group-header-comms')
    // If items not present, expand
    if (!screen.queryByTestId('group-items-comms')) {
      fireEvent.click(header)
    }
    const items = screen.getByTestId('group-items-comms')
    expect(items).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
    expect(screen.getByText('Calendar')).toBeInTheDocument()
    expect(screen.getByText('iMessage')).toBeInTheDocument()
    expect(screen.getByText('Slack')).toBeInTheDocument()
    expect(screen.getByText('GitHub')).toBeInTheDocument()
  })

  it('clicking a collapsed group header expands it and shows sub-items', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ comms: true }))
    renderSidebar()

    // Start collapsed
    expect(screen.queryByTestId('group-items-comms')).not.toBeInTheDocument()

    // Click header to expand
    fireEvent.click(screen.getByTestId('group-header-comms'))

    // Items should now be visible
    expect(screen.getByTestId('group-items-comms')).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
  })

  it('clicking an expanded group header collapses it and hides sub-items', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-comms')

    // Ensure expanded first
    if (!screen.queryByTestId('group-items-comms')) {
      fireEvent.click(header)
    }
    expect(screen.getByTestId('group-items-comms')).toBeInTheDocument()

    // Click to collapse
    fireEvent.click(header)
    expect(screen.queryByTestId('group-items-comms')).not.toBeInTheDocument()
  })

  it('toggle persists collapsed state to localStorage', () => {
    renderSidebar()
    const header = screen.getByTestId('group-header-comms')

    // Expand first if needed
    if (!screen.queryByTestId('group-items-comms')) {
      fireEvent.click(header)
    }

    // Collapse
    fireEvent.click(header)

    const saved = JSON.parse(localStorage.getItem('sidebar-group-collapsed') ?? '{}')
    expect(saved.comms).toBe(true)
  })

  it('toggle persists expanded state (false) to localStorage', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ comms: true }))
    renderSidebar()

    // Click to expand
    fireEvent.click(screen.getByTestId('group-header-comms'))

    const saved = JSON.parse(localStorage.getItem('sidebar-group-collapsed') ?? '{}')
    expect(saved.comms).toBe(false)
  })

  it('active route group (Comms) auto-expands on mount when collapsed in localStorage', () => {
    // Store comms as collapsed
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ comms: true }))
    // Render with a comms route active
    renderSidebar('/gmail')

    // Group items must be visible because /gmail belongs to comms
    expect(screen.getByTestId('group-items-comms')).toBeInTheDocument()
    expect(screen.getByText('Gmail')).toBeInTheDocument()
  })

  it('non-active group (Files) stays collapsed on mount when set in localStorage', () => {
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ files: true }))
    // Render at a non-files route
    renderSidebar('/')

    expect(screen.queryByTestId('group-items-files')).not.toBeInTheDocument()
  })

  it('every route is reachable: all links present when all groups expanded', () => {
    renderSidebar()
    // Ensure all groups are expanded (only expand if currently collapsed)
    expandAllGroups()

    const expectedLinks = [
      { label: 'Home', href: '/' },
      { label: 'Tasks', href: '/tasks' },
      { label: 'Agents', href: '/agents' },
      { label: 'Specs', href: '/specs' },
      { label: 'Gmail', href: '/gmail' },
      { label: 'Calendar', href: '/calendar' },
      { label: 'iMessage', href: '/imessage' },
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

  it('two group headers are rendered: files, comms (automation group removed)', () => {
    renderSidebar()
    expect(screen.getByTestId('group-header-files')).toBeInTheDocument()
    expect(screen.getByTestId('group-header-comms')).toBeInTheDocument()
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

  it('Comms group header shows rolled-up gmail badge when collapsed and gmail unread > 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/agents')) return Promise.resolve({ active: [], agents: [] })
      if (url === '/tasks/counts') return Promise.resolve({ open: 0 })
      if (url === '/gmail/auth/status') return Promise.resolve({ authenticated: true, unread_count: 5 })
      return Promise.resolve({})
    })
    localStorage.setItem('sidebar-group-collapsed', JSON.stringify({ comms: true }))
    renderSidebar()

    // Wait for gmail badge to load
    await waitFor(() => {
      const header = screen.getByTestId('group-header-comms')
      // Badge should appear inside the header
      expect(header.textContent).toContain('5')
    })
  })
})

// ------------- Regression guard for needle 293 (unchanged) -------------

describe('Sidebar health dot debouncing (needle 293)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'myOS',
      features: DEFAULT_FEATURES,
    })
  })

  const backendDot = () => {
    const label = screen.getByText('Backend')
    const row = label.parentElement
    if (!row) throw new Error('Backend row not found')
    const dot = row.querySelector('span.rounded-full')
    if (!dot) throw new Error('Backend dot not found')
    return dot as HTMLElement
  }

  const ostkDot = () => {
    const label = screen.getByText((_content, node) => {
      return !!node && node.tagName === 'SPAN' && (node.textContent ?? '').startsWith('System')
    })
    const row = label.parentElement
    if (!row) throw new Error('System row not found')
    const dot = row.querySelector('span.rounded-full')
    if (!dot) throw new Error('System dot not found')
    return dot as HTMLElement
  }

  const isRed = (el: HTMLElement) => el.className.includes('bg-red-400')
  const isGreen = (el: HTMLElement) => el.className.includes('bg-green-400')

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
    expect(isGreen(ostkDot())).toBe(true)
    expect(clockCalls).toBe(3)

    vi.useRealTimers()
  })

  it('flips red within five seconds when the backend is genuinely down', async () => {
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

    await vi.advanceTimersByTimeAsync(5_000)

    expect(isRed(backendDot())).toBe(true)
    expect(isRed(ostkDot())).toBe(true)

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

describe('Sidebar status panel does not expose Claude indicator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    _resetSidebarBus()
    useAppStore.setState({
      osName: 'myOS',
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

  it('shows Backend and System rows but not sessions and not a Claude row', async () => {
    renderSidebar()

    // Backend + System rows are rendered.
    await waitFor(() => {
      expect(screen.getByText('Backend')).toBeTruthy()
    })
    const systemRow = screen.getByText((_content, node) => {
      return !!node && node.tagName === 'SPAN' && (node.textContent ?? '').startsWith('System')
    })
    expect(systemRow).toBeTruthy()

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
      osName: 'myOS',
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

describe('Files and Drive sidebar entries', () => {
  it('shows Files entry in Files & Docs group', () => {
    renderSidebar()
    expandAllGroups()
    expect(screen.getByText('Files')).toBeInTheDocument()
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
})
