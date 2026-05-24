import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import NeedlesCountWidget from '../components/NeedlesCountWidget'
import ActiveAgentsWidget from '../components/ActiveAgentsWidget'
import QuickActionsWidget from '../components/QuickActionsWidget'

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../lib/agentUtils', async () => {
  const actual = await vi.importActual<typeof import('../lib/agentUtils')>('../lib/agentUtils')
  return actual
})

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

// ─── NeedlesCountWidget ───────────────────────────────────────────────────

describe('NeedlesCountWidget', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows loading state initially', () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    wrap(<NeedlesCountWidget />)
    expect(screen.getByTestId('needles-loading')).toBeInTheDocument()
  })

  it('renders counts on successful fetch', async () => {
    mockedGet.mockResolvedValue({
      counts: { open: 7, p0: 2, p1: 3, p2: 2, closed: 10 },
    })
    wrap(<NeedlesCountWidget />)
    await waitFor(() => expect(screen.getByTestId('needles-counts')).toBeInTheDocument())
    expect(screen.getByTestId('needles-open').textContent).toBe('7')
    expect(screen.getByTestId('needles-p0').textContent).toBe('2')
    expect(screen.getByTestId('needles-p1').textContent).toBe('3')
  })

  it('shows error message when fetch fails', async () => {
    mockedGet.mockRejectedValue(new Error('network error'))
    wrap(<NeedlesCountWidget />)
    await waitFor(() => expect(screen.getByTestId('needles-error')).toBeInTheDocument())
  })

  it('navigates to /backlog on click', async () => {
    mockedGet.mockResolvedValue({ counts: { open: 1, p0: 0, p1: 0, p2: 1, closed: 0 } })
    wrap(<NeedlesCountWidget />)
    await waitFor(() => expect(screen.getByTestId('needles-counts')).toBeInTheDocument())
    const user = userEvent.setup()
    await user.click(screen.getByText('Open needles'))
    expect(mockNavigate).toHaveBeenCalledWith('/backlog')
  })
})

// ─── ActiveAgentsWidget ───────────────────────────────────────────────────

describe('ActiveAgentsWidget', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows loading state initially', () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    wrap(<ActiveAgentsWidget />)
    expect(screen.getByTestId('agents-loading')).toBeInTheDocument()
  })

  it('renders count zero when no agents are running', async () => {
    mockedGet.mockResolvedValue({ agents: [], daemon_running: true, status: 'ok', active: [] })
    wrap(<ActiveAgentsWidget />)
    await waitFor(() => expect(screen.getByTestId('agents-content')).toBeInTheDocument())
    expect(screen.getByTestId('agents-count').textContent).toBe('0')
    expect(screen.getByText('No agents running right now.')).toBeInTheDocument()
  })

  it('renders running agent tasks', async () => {
    mockedGet.mockResolvedValue({
      agents: [
        { name: 'agent-1', status: 'running', source: 'claude-code', task: 'Fix login bug' },
        { name: 'agent-2', status: 'running', source: 'claude-code', task: 'Update docs' },
      ],
      daemon_running: true,
      status: 'ok',
      active: ['agent-1', 'agent-2'],
    })
    wrap(<ActiveAgentsWidget />)
    await waitFor(() => expect(screen.getByTestId('agents-list')).toBeInTheDocument())
    expect(screen.getByTestId('agents-count').textContent).toBe('2')
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
  })

  it('shows error message when fetch fails', async () => {
    mockedGet.mockRejectedValue(new Error('network error'))
    wrap(<ActiveAgentsWidget />)
    await waitFor(() => expect(screen.getByTestId('agents-error')).toBeInTheDocument())
  })

  it('navigates to /agents on click', async () => {
    mockedGet.mockResolvedValue({ agents: [], daemon_running: true, status: 'ok', active: [] })
    wrap(<ActiveAgentsWidget />)
    await waitFor(() => expect(screen.getByTestId('agents-content')).toBeInTheDocument())
    const user = userEvent.setup()
    await user.click(screen.getByText('Active agents'))
    expect(mockNavigate).toHaveBeenCalledWith('/agents')
  })
})

// ─── QuickActionsWidget ───────────────────────────────────────────────────

describe('QuickActionsWidget', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders all three action buttons', () => {
    wrap(<QuickActionsWidget onAddNeedle={() => {}} />)
    expect(screen.getByTestId('action-backlog')).toBeInTheDocument()
    expect(screen.getByTestId('action-agents')).toBeInTheDocument()
    expect(screen.getByTestId('action-add-needle')).toBeInTheDocument()
  })

  it('navigates to /backlog on Open Backlog click', async () => {
    const user = userEvent.setup()
    wrap(<QuickActionsWidget onAddNeedle={() => {}} />)
    await user.click(screen.getByTestId('action-backlog'))
    expect(mockNavigate).toHaveBeenCalledWith('/backlog')
  })

  it('navigates to /agents on Open Agents click', async () => {
    const user = userEvent.setup()
    wrap(<QuickActionsWidget onAddNeedle={() => {}} />)
    await user.click(screen.getByTestId('action-agents'))
    expect(mockNavigate).toHaveBeenCalledWith('/agents')
  })

  it('calls onAddNeedle when Add a needle is clicked', async () => {
    const onAddNeedle = vi.fn()
    const user = userEvent.setup()
    wrap(<QuickActionsWidget onAddNeedle={onAddNeedle} />)
    await user.click(screen.getByTestId('action-add-needle'))
    expect(onAddNeedle).toHaveBeenCalledTimes(1)
  })
})
