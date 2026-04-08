import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Agents from './Agents'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const mockAgentsResponse = {
  daemon_running: true,
  status: 'ok',
  active: ['test-agent'],
  agents: [
    {
      name: 'test-agent',
      status: 'running',
      source: 'daemon',
      model: 'sonnet',
      budget: '2.00',
      spawned_at: new Date(Date.now() - 83000).toISOString(),
      transcript_bytes: 12288,
      transcript_lines: 47,
    },
  ],
}

const mockTemplatesResponse = {
  templates: [],
}

function renderAgents() {
  return render(
    <MemoryRouter>
      <Agents />
    </MemoryRouter>
  )
}

describe('Agents page - Nudge feature', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'Hello agent',
        timestamp: '2026-04-04T21:00:00+00:00',
        source: 'ui',
        stdin_delivered: false,
      },
    })
  })

  it('renders active agent with nudge input', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    // The nudge input should be visible for active agents
    const input = screen.getByPlaceholderText('Send a message to this agent...')
    expect(input).toBeInTheDocument()
  })

  it('renders Send button for active agents', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const sendButton = screen.getByRole('button', { name: /Send/i })
    expect(sendButton).toBeInTheDocument()
  })

  it('sends nudge when clicking Send button', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Send a message to this agent...')
    fireEvent.change(input, { target: { value: 'Hello agent' } })

    const sendButton = screen.getByRole('button', { name: /Send/i })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/nudge', {
        message: 'Hello agent',
      })
    })
  })

  it('sends nudge when pressing Enter', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Send a message to this agent...')
    fireEvent.change(input, { target: { value: 'Enter nudge' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/nudge', {
        message: 'Enter nudge',
      })
    })
  })

  it('clears input after sending nudge', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Send a message to this agent...') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Clear me' } })

    const sendButton = screen.getByRole('button', { name: /Send/i })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('does not send nudge with empty input', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    // Input is empty, click send
    const sendButton = screen.getByRole('button', { name: /Send/i })
    fireEvent.click(sendButton)

    // api.post should not have been called for nudge (only for spawn if any)
    expect(mockedApiPost).not.toHaveBeenCalledWith(
      '/agents/test-agent/nudge',
      expect.anything()
    )
  })

  it('shows sent nudge in the output area', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Send a message to this agent...')
    fireEvent.change(input, { target: { value: 'Hello agent' } })

    const sendButton = screen.getByRole('button', { name: /Send/i })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(screen.getByText('Hello agent')).toBeInTheDocument()
    })

    // The "You:" label should appear
    expect(screen.getByText('You:')).toBeInTheDocument()
  })

  it('shows Expand/Collapse button on active agent cards', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    expect(expandButton).toBeInTheDocument()
  })

  it('expands agent details when clicking Expand', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    fireEvent.click(expandButton)

    await waitFor(() => {
      expect(screen.getByText('Messages sent')).toBeInTheDocument()
    })
  })

  it('shows no nudge input when no active agents', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(
        screen.getByText('No active agents. Spawn one to get started.')
      ).toBeInTheDocument()
    })

    expect(
      screen.queryByPlaceholderText('Send a message to this agent...')
    ).not.toBeInTheDocument()
  })
})

describe('Agents page - Status bar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
  })

  it('renders the status bar for active agents', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('agent-status-bar')).toBeInTheDocument()
    })
  })

  it('displays model name in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('sonnet')
    })
  })

  it('displays budget cap in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('$2.00 cap')
    })
  })

  it('displays transcript size in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('12.0KB')
    })
  })

  it('displays transcript line count in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('47 lines')
    })
  })

  it('displays elapsed time in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      // Should show something like "1:23" (elapsed time from spawned_at)
      expect(statusBar.textContent).toMatch(/\d+:\d{2}/)
    })
  })

  it('does not render status bar when no spawned_at', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['no-time-agent'],
        agents: [{ name: 'no-time-agent', status: 'running', source: 'daemon', model: 'sonnet' }],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'no-time-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('no-time-agent')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('agent-status-bar')).not.toBeInTheDocument()
  })
})

describe('Agents page - tabs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows Active, Recent, and Metrics tabs', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument()
      expect(screen.getByText('Recent')).toBeInTheDocument()
      expect(screen.getByText('Metrics')).toBeInTheDocument()
    })
  })

})

describe('Agents page - Recent tab filtering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows only completed agents in the Recent tab', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['running-agent'],
        agents: [
          { name: 'running-agent', status: 'running', source: 'daemon', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'completed-agent', status: 'completed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'stopped-agent', status: 'stopped', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'abandoned-agent', status: 'abandoned', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByText('completed-agent')).toBeInTheDocument()
    })

    expect(screen.queryByText('stopped-agent')).not.toBeInTheDocument()
    expect(screen.queryByText('abandoned-agent')).not.toBeInTheDocument()
    // running-agent is on Active tab, not Recent
    expect(screen.queryByText('running-agent')).not.toBeInTheDocument()
  })

  it('shows empty state in Recent tab when no completed agents exist', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'stopped-agent', status: 'stopped', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'abandoned-agent', status: 'abandoned', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByText('No completed agents yet. Agents you spawn will appear here once they finish.')).toBeInTheDocument()
    })
  })
})

const mockPmTemplatesResponse = {
  templates: [
    {
      id: 'builtin-research-spike',
      name: 'Research spike',
      description: 'Research a topic thoroughly and write a 1-page summary.',
      icon: 'science',
      prompt_template: 'Research [topic] thoroughly. Find key facts, trade-offs, and recommendations. Write a 1-page summary.',
      model: 'sonnet',
      budget: 2.0,
      builtin: true,
    },
    {
      id: 'custom-abc123',
      name: 'My Custom',
      description: 'Does things',
      icon: 'smart_toy',
      prompt_template: 'Do [thing] for me.',
      model: 'sonnet',
      budget: 1.0,
      builtin: false,
    },
  ],
}

describe('Agents page - Templates tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/pm-templates') return mockPmTemplatesResponse
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'ok' })
  })

  it('shows Templates tab in navigation', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Templates' })).toBeInTheDocument()
    })
  })

  it('shows PM Templates heading when Templates tab is active', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('PM Templates')).toBeInTheDocument()
    })
  })

  it('shows built-in templates when Templates tab is active', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
  })

  it('shows custom templates in "Your templates" section', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('My Custom')).toBeInTheDocument()
    })
  })

  it('shows Use button for built-in templates', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      const useButtons = screen.getAllByRole('button', { name: 'Use' })
      expect(useButtons.length).toBeGreaterThan(0)
    })
  })

  it('clicking Use on a built-in template switches to Active tab and pre-fills name', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    const useButtons = screen.getAllByRole('button', { name: 'Use' })
    fireEvent.click(useButtons[0])

    await waitFor(() => {
      // Should switch back to Active tab and show the spawn form
      expect(screen.getByText('Active Sessions')).toBeInTheDocument()
    })
  })

  it('shows filter input on Templates tab', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Find a template...')).toBeInTheDocument()
    })
  })

  it('filters templates by search term', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText('Find a template...')
    fireEvent.change(searchInput, { target: { value: 'research' } })

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
  })

  it('shows New template button in Your templates section', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /New template/i })).toBeInTheDocument()
    })
  })

  it('shows prompt_template text on template cards', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText(/Research \[topic\] thoroughly/)).toBeInTheDocument()
    })
  })
})
