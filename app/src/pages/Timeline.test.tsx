import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Timeline from './Timeline'
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

function renderTimeline() {
  return render(
    <MemoryRouter>
      <Timeline />
    </MemoryRouter>
  )
}

describe('Timeline page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'YourOS', darkMode: true })
  })

  it('groups tasks by goal, not by raw tags', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Scaffold project', priority: 'P0', status: 'open', created_at: '2026-04-10T00:00:00Z', tags: ['phase-1', 'foundation'], goal: 'Foundation' },
        { id: '2', title: 'Build chat panel', priority: 'P0', status: 'open', created_at: '2026-04-12T00:00:00Z', tags: ['phase-2', 'chat'], goal: 'Chat' },
        { id: '3', title: 'Connect chat to ostk', priority: 'P0', status: 'open', created_at: '2026-04-13T00:00:00Z', tags: ['phase-2', 'chat'], goal: 'Chat' },
      ],
    })

    renderTimeline()

    await waitFor(() => {
      expect(screen.getByText('Foundation')).toBeInTheDocument()
    })
    expect(screen.getByText('Chat')).toBeInTheDocument()

    // Phase tags should NOT appear as group names
    expect(screen.queryByText('phase-1')).not.toBeInTheDocument()
    expect(screen.queryByText('phase-2')).not.toBeInTheDocument()
  })

  it('shows "Untagged" for tasks with no goal', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Orphan task', priority: 'P1', status: 'open', created_at: '2026-04-10T00:00:00Z', tags: [], goal: null },
      ],
    })

    renderTimeline()

    await waitFor(() => {
      expect(screen.getByText('Untagged')).toBeInTheDocument()
    })
    // Task row renders as "{id} {title}" in one element
    expect(screen.getByText(/Orphan task/)).toBeInTheDocument()
  })

  it('displays goal groups with their task counts', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Task A', priority: 'P0', status: 'open', created_at: '2026-04-10T00:00:00Z', tags: ['chat'], goal: 'Chat' },
        { id: '2', title: 'Task B', priority: 'P1', status: 'open', created_at: '2026-04-11T00:00:00Z', tags: ['chat'], goal: 'Chat' },
        { id: '3', title: 'Task C', priority: 'P1', status: 'open', created_at: '2026-04-12T00:00:00Z', tags: ['agents'], goal: 'Agents' },
      ],
    })

    renderTimeline()

    await waitFor(() => {
      expect(screen.getByText('Chat')).toBeInTheDocument()
    })
    expect(screen.getByText('Agents')).toBeInTheDocument()

    // The gantt bar label shows "N tasks" or "N task"
    expect(screen.getByText('2 tasks')).toBeInTheDocument()
    expect(screen.getByText('1 task')).toBeInTheDocument()
  })

  it('shows empty message when no tasks match', async () => {
    mockedApiGet.mockResolvedValue({ tasks: [] })

    renderTimeline()

    await waitFor(() => {
      expect(screen.getByText(/No tasks to display/)).toBeInTheDocument()
    })
  })

  it('uses human-friendly goal names from the API', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Fix images', priority: 'P1', status: 'open', created_at: '2026-04-05T00:00:00Z', tags: ['lego-app'], goal: 'Lego App' },
        { id: '2', title: 'Build bridge', priority: 'P0', status: 'open', created_at: '2026-04-06T00:00:00Z', tags: ['phase-2', 'ostk'], goal: 'ostk Bridge' },
      ],
    })

    renderTimeline()

    await waitFor(() => {
      expect(screen.getByText('Lego App')).toBeInTheDocument()
    })
    expect(screen.getByText('ostk Bridge')).toBeInTheDocument()

    // Raw tag names should not be used as headings
    expect(screen.queryByText('lego-app')).not.toBeInTheDocument()
  })
})
