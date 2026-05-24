import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TeamHome from './TeamHome'

const mockTeamData = {
  team_picks: [
    { id: 'p1', name: 'code-reviewer', version: 2, published_at: '2026-04-01T00:00:00Z', signed_by_org: 'admin' },
    { id: 'p2', name: 'planner', version: 1, published_at: '2026-03-01T00:00:00Z', signed_by_org: 'admin' },
  ],
  announcements: [
    { id: 'a1', title: 'Welcome to the team', body: 'Great to have you here.' },
  ],
  adoption_summary: {
    active_members_this_week: 3,
    top_skill: 'code-reviewer',
  },
}

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

vi.mock('../stores/app', () => ({
  useAppStore: (selector: (s: { instanceMode: string; darkMode: boolean }) => unknown) =>
    selector({ instanceMode: 'team', darkMode: false }),
}))

vi.mock('../components/Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`}>{name}</span>,
}))

import { api } from '../lib/api'

const mockApi = api as { get: ReturnType<typeof vi.fn> }

function renderPage() {
  return render(
    <MemoryRouter>
      <TeamHome />
    </MemoryRouter>
  )
}

describe('TeamHome', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading spinner initially', () => {
    mockApi.get.mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByTestId('team-home-loading')).toBeInTheDocument()
  })

  it('renders main page after data loads', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('team-home')).toBeInTheDocument()
    })
    expect(screen.getByText('Your Team')).toBeInTheDocument()
  })

  it('renders team picks card', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('team-picks-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('team-picks-list')).toBeInTheDocument()
    expect(screen.getByTestId('pick-p1')).toBeInTheDocument()
    expect(screen.getByTestId('pick-p2')).toBeInTheDocument()
    expect(screen.getByText('planner')).toBeInTheDocument()
  })

  it('shows empty state when no picks', async () => {
    mockApi.get.mockResolvedValue({ ...mockTeamData, team_picks: [] })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('team-picks-empty')).toBeInTheDocument()
    })
  })

  it('renders adoption summary', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('adoption-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('active-members-count')).toHaveTextContent('3')
    expect(screen.getByTestId('top-skill')).toHaveTextContent('code-reviewer')
  })

  it('renders announcements card', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('announcements-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('announcements-list')).toBeInTheDocument()
    expect(screen.getByText('Welcome to the team')).toBeInTheDocument()
    expect(screen.getByText('Great to have you here.')).toBeInTheDocument()
  })

  it('shows empty state when no announcements', async () => {
    mockApi.get.mockResolvedValue({ ...mockTeamData, announcements: [] })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('announcements-empty')).toBeInTheDocument()
    })
  })

  it('shows error message on API failure', async () => {
    mockApi.get.mockRejectedValue(new Error('Network error'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('team-home-error')).toBeInTheDocument()
    })
    expect(screen.getByText('Network error')).toBeInTheDocument()
  })

  it('renders team page content in team mode (guard is wired)', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('team-home')).toBeInTheDocument()
    })
  })

  it('calls /team/home on mount', async () => {
    mockApi.get.mockResolvedValue(mockTeamData)
    renderPage()
    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith('/team/home')
    })
  })
})
