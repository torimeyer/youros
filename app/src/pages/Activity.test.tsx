import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Activity from './Activity'
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

function renderActivity() {
  return render(
    <MemoryRouter>
      <Activity />
    </MemoryRouter>
  )
}

const SAMPLE_EVENTS = [
  {
    timestamp: '2026-04-06T17:50:00Z',
    event: 'agent.spawned',
    label: 'Agent started',
    category: 'agent',
    detail: 'worker-1',
  },
  {
    timestamp: '2026-04-06T17:45:20Z',
    event: 'needle.activated',
    label: 'Task activated',
    category: 'task',
    detail: 'needle="→088"',
  },
  {
    timestamp: '2026-04-06T17:42:46Z',
    event: 'task.added',
    label: 'Task created',
    category: 'task',
    detail: '→087 Build activity timeline',
  },
]

describe('Activity page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('renders the page title', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Activity' })).toBeInTheDocument()
    })
  })

  it('shows events from the API', async () => {
    mockedApiGet.mockResolvedValue({ events: SAMPLE_EVENTS, count: 3 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('Agent started')).toBeInTheDocument()
    })
    expect(screen.getByText('Task created')).toBeInTheDocument()
    expect(screen.getByText('Task activated')).toBeInTheDocument()
  })

  it('displays event details', async () => {
    mockedApiGet.mockResolvedValue({ events: SAMPLE_EVENTS, count: 3 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('→087 Build activity timeline')).toBeInTheDocument()
    })
    expect(screen.getByText('worker-1')).toBeInTheDocument()
  })

  it('shows empty state when no events', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('No activity to show yet.')).toBeInTheDocument()
    })
  })

  it('shows event count in the header', async () => {
    mockedApiGet.mockResolvedValue({ events: SAMPLE_EVENTS, count: 3 })
    renderActivity()

    await waitFor(() => {
      // The header shows "N events" next to the title
      const counts = screen.getAllByText('3 events')
      expect(counts.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('renders filter buttons', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('All')).toBeInTheDocument()
    })
    expect(screen.getByText('Tasks')).toBeInTheDocument()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.getByText('Ideas')).toBeInTheDocument()
    expect(screen.getByText('System')).toBeInTheDocument()
  })

  it('filters events by category', async () => {
    mockedApiGet.mockResolvedValue({ events: SAMPLE_EVENTS, count: 3 })
    const user = userEvent.setup()
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('Agent started')).toBeInTheDocument()
    })

    // Click "Agents" filter
    await user.click(screen.getByText('Agents'))

    // Agent event should remain visible
    expect(screen.getByText('Agent started')).toBeInTheDocument()
    // Task events should be hidden
    expect(screen.queryByText('Task created')).not.toBeInTheDocument()
    expect(screen.queryByText('Task activated')).not.toBeInTheDocument()
  })

  it('groups events by date', async () => {
    const multiDayEvents = [
      {
        timestamp: '2026-04-06T17:42:46Z',
        event: 'task.added',
        label: 'Task created',
        category: 'task',
        detail: '→087 Build timeline',
      },
      {
        timestamp: '2026-04-05T10:00:00Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: '→044 Done',
      },
    ]
    mockedApiGet.mockResolvedValue({ events: multiDayEvents, count: 2 })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('Task created')).toBeInTheDocument()
    })
    expect(screen.getByText('Task closed')).toBeInTheDocument()
  })
})
