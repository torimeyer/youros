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

describe('Agents page - Permissions tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows Permissions tab', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: [], status_filter: 'pending' }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Permissions')).toBeInTheDocument()
    })
  })

  it('shows empty state when no pending requests', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: [], status_filter: 'pending' }
      return {}
    })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('No pending requests. Agents will ask for permission here when they need extra access.')).toBeInTheDocument()
    })
  })

  it('renders grant cards with approve and deny buttons', async () => {
    const mockGrants = [
      {
        id: 'g-100',
        type: 'file_access',
        agent: 'research-bot',
        target: '/etc/hosts',
        status: 'pending',
        requested_at: '2026-04-06T10:00:00Z',
      },
    ]

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: mockGrants, status_filter: 'pending' }
      return {}
    })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('research-bot')).toBeInTheDocument()
    })

    expect(screen.getByText('/etc/hosts')).toBeInTheDocument()
    expect(screen.getByText('File access')).toBeInTheDocument()
    // The grant card should contain Approve and Deny action buttons
    const grantCard = screen.getByTestId('grant-card')
    expect(grantCard.querySelector('button')).toBeTruthy()
    expect(grantCard.textContent).toContain('Approve')
    expect(grantCard.textContent).toContain('Deny')
  })

  it('calls approve endpoint when clicking Approve', async () => {
    const mockGrants = [
      {
        id: 'g-200',
        type: 'tool',
        agent: 'builder',
        target: 'bash',
        status: 'pending',
        requested_at: '2026-04-06T10:00:00Z',
      },
    ]

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: mockGrants, status_filter: 'pending' }
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'approved', grant_id: 'g-200', action: 'approved' })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('builder')).toBeInTheDocument()
    })

    const grantCard = screen.getByTestId('grant-card')
    const approveBtn = grantCard.querySelector('button')!
    fireEvent.click(approveBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/grants/g-200/approve')
    })
  })

  it('calls deny endpoint when clicking Deny', async () => {
    const mockGrants = [
      {
        id: 'g-300',
        type: 'secret',
        agent: 'spy-agent',
        target: 'PASSWORD',
        status: 'pending',
        requested_at: '2026-04-06T10:00:00Z',
      },
    ]

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: mockGrants, status_filter: 'pending' }
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'denied', grant_id: 'g-300', action: 'denied' })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('spy-agent')).toBeInTheDocument()
    })

    const grantCard = screen.getByTestId('grant-card')
    const buttons = grantCard.querySelectorAll('button')
    // Second button is Deny (first is Approve)
    fireEvent.click(buttons[1])

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/grants/g-300/deny')
    })
  })

  it('does not show approve/deny buttons for already-resolved grants', async () => {
    const mockGrants = [
      {
        id: 'g-400',
        type: 'budget',
        agent: 'spender',
        target: '$50',
        status: 'granted',
        requested_at: '2026-04-06T09:00:00Z',
      },
    ]

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: mockGrants, status_filter: 'granted' }
      return {}
    })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('spender')).toBeInTheDocument()
    })

    // The grant card should not have Approve/Deny action buttons
    // (filter buttons "Approved"/"Denied" will still be present)
    const grantCard = screen.getByTestId('grant-card')
    const cardButtons = grantCard.querySelectorAll('button')
    expect(cardButtons.length).toBe(0)
  })

  it('shows filter buttons for Waiting, Approved, and Denied', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: [], status_filter: 'pending' }
      return {}
    })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('Waiting')).toBeInTheDocument()
      expect(screen.getByText('Approved')).toBeInTheDocument()
      expect(screen.getByText('Denied')).toBeInTheDocument()
    })
  })

  it('shows correct type labels for different grant types', async () => {
    const mockGrants = [
      { id: 'g-501', type: 'file_access', agent: 'a1', target: '/tmp', status: 'pending', requested_at: '' },
      { id: 'g-502', type: 'tool', agent: 'a2', target: 'npm', status: 'pending', requested_at: '' },
      { id: 'g-503', type: 'budget', agent: 'a3', target: '$10', status: 'pending', requested_at: '' },
      { id: 'g-504', type: 'secret', agent: 'a4', target: 'KEY', status: 'pending', requested_at: '' },
      { id: 'g-505', type: 'model_upgrade', agent: 'a5', target: 'opus', status: 'pending', requested_at: '' },
    ]

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/grants')) return { grants: mockGrants, status_filter: 'pending' }
      return {}
    })

    renderAgents()

    const permTab = screen.getByText('Permissions')
    fireEvent.click(permTab)

    await waitFor(() => {
      expect(screen.getByText('File access')).toBeInTheDocument()
      expect(screen.getByText('Tool usage')).toBeInTheDocument()
      expect(screen.getByText('Budget increase')).toBeInTheDocument()
      expect(screen.getByText('Secret access')).toBeInTheDocument()
      expect(screen.getByText('Model upgrade')).toBeInTheDocument()
    })
  })
})
