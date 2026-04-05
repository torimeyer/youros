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
      osName: 'YourOS',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Hay/Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Docs', enabled: true },
        { label: 'Transcripts', enabled: true },
      ],
    })
    mockedApiGet.mockResolvedValue({ active: [] })
  })

  it('renders all navigation items when all features enabled', async () => {
    renderSidebar()

    const navLabels = ['Home', 'Tasks', 'Timeline', 'Ideas', 'Agents', 'Files', 'Transcripts', 'Settings']
    for (const label of navLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('renders the OS name from the store', () => {
    renderSidebar()
    expect(screen.getByText('YourOS')).toBeInTheDocument()
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
      Timeline: '/timeline',
      Ideas: '/ideas',
      Agents: '/agents',
      Files: '/files',
      Transcripts: '/transcripts',
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
})
