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
