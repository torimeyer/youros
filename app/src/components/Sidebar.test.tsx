import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
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

const mockedApiGet = vi.mocked(api.get)

function renderSidebar() {
  return render(
    <BrowserRouter>
      <Sidebar />
    </BrowserRouter>
  )
}

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'myOS',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Hay/Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Drive', enabled: true },
        { label: 'Calendar', enabled: true },
        { label: 'Gmail', enabled: true },
        { label: 'Docs', enabled: true },
        { label: 'Transcripts', enabled: true },
        { label: 'Automations', enabled: true },
      ],
    })
    mockedApiGet.mockResolvedValue({ active: [] })
  })

  it('renders all navigation items when all features enabled', async () => {
    renderSidebar()

    const navLabels = ['Home', 'Tasks', 'Activity', 'Ideas', 'Agents', 'Files', 'Drive', 'Calendar', 'Gmail', 'History', 'Automations', 'Settings']
    for (const label of navLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
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

  it('all nav items are links with correct paths', () => {
    renderSidebar()

    const expectedPaths: Record<string, string> = {
      Home: '/',
      Tasks: '/tasks',
      Activity: '/activity',
      Ideas: '/ideas',
      Agents: '/agents',
      Files: '/files',
      Drive: '/drive',
      Calendar: '/calendar',
      Gmail: '/gmail',
      History: '/transcripts',
      Automations: '/workflows',
      Settings: '/settings',
    }

    for (const [label, path] of Object.entries(expectedPaths)) {
      const link = screen.getByText(label).closest('a')
      expect(link).toHaveAttribute('href', path)
    }
  })

  it('active link gets highlighted class', () => {
    // BrowserRouter defaults to "/" which means Home should be active
    window.history.pushState({}, '', '/')
    renderSidebar()

    const homeLink = screen.getByText('Home').closest('a')
    expect(homeLink?.className).toContain('accent-highlight')
    expect(homeLink?.className).toContain('accent-border')
  })

  it('inactive links have inactive styling', () => {
    window.history.pushState({}, '', '/')
    renderSidebar()

    const tasksLink = screen.getByText('Tasks').closest('a')
    expect(tasksLink?.className).toContain('text-slate-400')
    expect(tasksLink?.className).not.toContain('accent-highlight')
  })

  it('does not show agent badge when activeAgents is 0', async () => {
    mockedApiGet.mockResolvedValue({ active: [] })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/agents')
    })

    // The badge number should not appear
    const agentsLink = screen.getByText('Agents').closest('a')
    expect(agentsLink?.querySelector('.animate-pulse')).toBeNull()
  })

  it('shows agent badge when activeAgents > 0', async () => {
    mockedApiGet.mockResolvedValue({ active: ['agent-1', 'agent-2'] })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('shows correct count for multiple active agents', async () => {
    mockedApiGet.mockResolvedValue({ active: ['a1', 'a2', 'a3', 'a4', 'a5'] })
    renderSidebar()

    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument()
    })
  })

  it('handles API error for agents gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderSidebar()

    // Should render without crashing, badge count stays at 0
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/agents')
    })

    expect(screen.getByText('Agents')).toBeInTheDocument()
  })

  it('fetches agents on mount', async () => {
    mockedApiGet.mockResolvedValue({ active: [] })
    renderSidebar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/agents')
    })
  })

  it('polls agents every 5 seconds so the badge stays up to date', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/agents') return Promise.resolve({ active: [] })
      return Promise.resolve({ authenticated: false, unread_count: 0 })
    })
    renderSidebar()

    // Count only /agents calls since the Sidebar also polls Gmail auth status
    const agentCalls = () => mockedApiGet.mock.calls.filter(c => c[0] === '/agents').length

    // Initial fetch
    await vi.advanceTimersByTimeAsync(0)
    expect(agentCalls()).toBe(1)

    // After 5 seconds, should poll again
    await vi.advanceTimersByTimeAsync(5000)
    expect(agentCalls()).toBe(2)

    // After another 5 seconds, third poll
    await vi.advanceTimersByTimeAsync(5000)
    expect(agentCalls()).toBe(3)

    vi.useRealTimers()
  })

  it('renders the What\'s New button in the sidebar footer', () => {
    renderSidebar()
    expect(screen.getByTestId('whats-new-button')).toBeInTheDocument()
  })

  it('renders the What\'s New button before the Tour button in DOM order', () => {
    renderSidebar()
    const whatsNewButton = screen.getByTestId('whats-new-button')
    const tourButton = screen.getByTestId('tour-button')

    // compareDocumentPosition returns DOCUMENT_POSITION_FOLLOWING (4) when
    // the argument node follows the context node. That is what we want here
    // since WhatsNew should come first in the sidebar footer.
    const position = whatsNewButton.compareDocumentPosition(tourButton)
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('updates badge count when API response changes between polls', async () => {
    // Start with no active agents, then after re-fetch return 3
    let agentCallCount = 0
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/agents') {
        agentCallCount++
        if (agentCallCount === 1) return { active: [] }
        return { active: ['a1', 'a2', 'a3'] }
      }
      return { authenticated: false, unread_count: 0 }
    })

    renderSidebar()

    // Wait for initial fetch
    await waitFor(() => {
      expect(agentCallCount).toBeGreaterThanOrEqual(1)
    })
    expect(screen.queryByText('3')).not.toBeInTheDocument()

    // Wait for the polling interval to fire and update the badge
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument()
    }, { timeout: 10000 })
  }, 15000)
})

// Regression guard for needle 293. After a fast "restart torios" (kill
// and respawn scripts/dev-backend.sh plus scripts/dev-frontend.sh in
// under three seconds), the sidebar Backend and ostk dots must stay
// green the entire time. A single red frame is a bug, because it has
// now regressed three times (needles 286, 287, 293). The underlying
// fix is that the polling effect in Sidebar.tsx requires two
// consecutive failures before flipping the dot red, so a single
// transient failure during a restart window is invisible to the user
// while a genuinely down backend still turns red within five seconds.
describe('Sidebar health dot debouncing (needle 293)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'myOS',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Hay/Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Drive', enabled: true },
        { label: 'Calendar', enabled: true },
        { label: 'Gmail', enabled: true },
        { label: 'Docs', enabled: true },
        { label: 'Transcripts', enabled: true },
        { label: 'Automations', enabled: true },
      ],
    })
  })

  // Locate the Backend label's sibling dot. The dot is the first child
  // of the flex row that contains the "Backend" text node. The label
  // lives in a <span>, the dot is its previous sibling <span>.
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
      return !!node && node.tagName === 'SPAN' && (node.textContent ?? '').startsWith('ostk')
    })
    const row = label.parentElement
    if (!row) throw new Error('ostk row not found')
    const dot = row.querySelector('span.rounded-full')
    if (!dot) throw new Error('ostk dot not found')
    return dot as HTMLElement
  }

  const isRed = (el: HTMLElement) => el.className.includes('bg-red-400')
  const isGreen = (el: HTMLElement) => el.className.includes('bg-green-400')

  // Track the backend dot class across every timer tick so a flash
  // between frames cannot be missed. React state updates are
  // synchronous inside advanceTimersByTimeAsync, so after every step
  // we snapshot the className and store it.
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
        // First call succeeds (initial mount, backend up).
        if (clockCalls === 1) return { kernel: 'v2.5.0' }
        // Second call fails (Tori killed uvicorn, proxy returns 502).
        if (clockCalls === 2) throw new Error('Upstream unavailable')
        // Third call succeeds (backend is back up within three seconds).
        return { kernel: 'v2.5.0' }
      }
      if (url === '/agents') return { active: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    renderSidebar()

    // Flush microtasks so the initial mount fetch resolves and the
    // first success paints the dot green. advanceTimersByTimeAsync
    // with a small positive value drains both the setTimeout queue
    // and the pending microtasks, so two ticks is enough.
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isGreen(backendDot())).toBe(true)

    // Advance to the next success-interval poll. The success interval
    // is 15 seconds, and this time the mock throws to simulate the
    // killed backend. With the needle 293 debounce, a single failure
    // must NOT flip the dot red.
    const states = await collectDotStates([
      // Fire the second poll at t = 15s. It fails.
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      // Fire the third poll at t = 17s (failure interval is 2s). It
      // succeeds because the backend is back.
      async () => { await vi.advanceTimersByTimeAsync(2_000) },
    ])

    // The dot must never have been red at any snapshot point.
    for (const className of states) {
      expect(className).not.toContain('bg-red-400')
    }
    // And at the end it must still be green.
    expect(isGreen(backendDot())).toBe(true)
    // The ostk dot must also stay green.
    expect(isGreen(ostkDot())).toBe(true)
    // All three polls actually ran.
    expect(clockCalls).toBe(3)

    vi.useRealTimers()
  })

  it('flips red within five seconds when the backend is genuinely down', async () => {
    vi.useFakeTimers()
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/status/clock') {
        // Every call fails. Backend is truly gone.
        throw new Error('ECONNREFUSED')
      }
      if (url === '/agents') return { active: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    renderSidebar()

    // Flush microtasks so the initial mount fetch rejects. The first
    // failure is tolerated by the debounce, so the dot must NOT be
    // red yet. Note: initial state is null (grey), not green, because
    // nothing has ever succeeded.
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isRed(backendDot())).toBe(false)

    // Advance five full seconds of wall time. The second failure fires
    // at t = 2s (failure interval), which crosses the threshold and
    // flips the dot red. The test asserts red is set no later than
    // five seconds after mount, which is the agreed ceiling.
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
        // Call 1: success on mount.
        if (clockCalls === 1) return { kernel: 'v2.5.0' }
        // Call 2: transient failure.
        if (clockCalls === 2) throw new Error('Upstream unavailable')
        // Call 3: success again, consecutive counter resets.
        if (clockCalls === 3) return { kernel: 'v2.5.0' }
        // Call 4: another transient failure. Still only one in a row,
        // so the dot must stay green.
        if (clockCalls === 4) throw new Error('Upstream unavailable')
        // Call 5 onwards: success forever.
        return { kernel: 'v2.5.0' }
      }
      if (url === '/agents') return { active: [] }
      if (url === '/gmail/auth/status') return { authenticated: false, unread_count: 0 }
      if (url === '/upgrade/status') return { myos: { current: 'v1.0.0' } }
      return {}
    })

    renderSidebar()

    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(isGreen(backendDot())).toBe(true)

    const states = await collectDotStates([
      // Call 2 fires after the success interval. It fails.
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      // Call 3 fires after the failure interval. It succeeds.
      async () => { await vi.advanceTimersByTimeAsync(2_000) },
      // Call 4 fires after the next success interval. It fails again,
      // but the consecutive counter was reset by call 3, so this is
      // still only a single failure in a row.
      async () => { await vi.advanceTimersByTimeAsync(15_000) },
      // Call 5 fires after the failure interval. It succeeds.
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
