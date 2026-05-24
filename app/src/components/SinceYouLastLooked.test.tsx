import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SinceYouLastLooked, { computeFinishedCount } from './SinceYouLastLooked'

// --------------- unit: computeFinishedCount ---------------

describe('computeFinishedCount', () => {
  // API returns only completed agents; the function filters purely on completed_at timing.
  const agents = [
    { name: 'a1', status: 'completed', completed_at: '2026-04-27T10:00:00Z' },
    { name: 'a2', status: 'completed', completed_at: '2026-04-27T09:00:00Z' },
    { name: 'a3', status: 'completed', completed_at: '2026-04-27T08:00:00Z' },
    { name: 'a4', status: 'completed' },  // no completed_at
  ]

  it('returns 0 when lastViewed is empty', () => {
    expect(computeFinishedCount(agents, '')).toBe(0)
  })

  it('returns 0 when lastViewed is null', () => {
    expect(computeFinishedCount(agents, null)).toBe(0)
  })

  it('counts agents with completed_at strictly after lastViewed', () => {
    // only a1 (10:00) is after 09:30
    expect(computeFinishedCount(agents, '2026-04-27T09:30:00Z')).toBe(1)
  })

  it('counts multiple agents newer than lastViewed', () => {
    // a1, a2, a3 are after 07:00; a4 has no completed_at so excluded
    expect(computeFinishedCount(agents, '2026-04-27T07:00:00Z')).toBe(3)
  })

  it('excludes agents without completed_at', () => {
    // a4 has no completed_at, so excluded even with old lastViewed
    expect(computeFinishedCount(agents, '2026-04-26T00:00:00Z')).toBe(3)
  })

  it('returns 0 when all agents are older than lastViewed', () => {
    expect(computeFinishedCount(agents, '2026-04-28T00:00:00Z')).toBe(0)
  })
})

// --------------- component rendering ---------------

// Mock the app store
const mockSetAgentsLastViewed = vi.fn()
let mockAgentsLastViewed = '2026-04-27T09:30:00Z'

vi.mock('../stores/app', () => ({
  useAppStore: (selector: (s: { agentsLastViewed: string; setAgentsLastViewed: () => void }) => unknown) =>
    selector({
      agentsLastViewed: mockAgentsLastViewed,
      setAgentsLastViewed: mockSetAgentsLastViewed,
    }),
}))

// Mock the api
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockApi = api as { get: ReturnType<typeof vi.fn> }

function renderComponent() {
  return render(
    <MemoryRouter>
      <SinceYouLastLooked />
    </MemoryRouter>,
  )
}

describe('SinceYouLastLooked component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAgentsLastViewed = '2026-04-27T09:30:00Z'
  })

  it('renders nothing when no agents finished after lastViewed', async () => {
    mockApi.get.mockResolvedValue({
      agents: [
        { name: 'old', status: 'completed', completed_at: '2026-04-27T08:00:00Z' },
      ],
    })
    renderComponent()
    await waitFor(() => {
      expect(screen.queryByTestId('since-you-last-looked-button')).toBeNull()
    })
  })

  it('renders badge with count when agents finished after lastViewed', async () => {
    mockApi.get.mockResolvedValue({
      agents: [
        { name: 'new1', status: 'completed', completed_at: '2026-04-27T10:00:00Z' },
        { name: 'new2', status: 'completed', completed_at: '2026-04-27T11:00:00Z' },
        { name: 'old', status: 'completed', completed_at: '2026-04-27T08:00:00Z' },
      ],
    })
    renderComponent()
    await waitFor(() => {
      expect(screen.getByTestId('since-you-last-looked-badge')).toBeTruthy()
      expect(screen.getByTestId('since-you-last-looked-badge').textContent).toBe('2')
    })
  })

  it('renders "9+" when more than 9 agents finished', async () => {
    const agents = Array.from({ length: 11 }, (_, i) => ({
      name: `agent-${i}`,
      status: 'completed',
      completed_at: '2026-04-27T10:00:00Z',
    }))
    mockApi.get.mockResolvedValue({ agents })
    renderComponent()
    await waitFor(() => {
      expect(screen.getByTestId('since-you-last-looked-badge').textContent).toBe('9+')
    })
  })

  it('renders nothing when agentsLastViewed is empty', async () => {
    mockAgentsLastViewed = ''
    mockApi.get.mockResolvedValue({ agents: [] })
    renderComponent()
    await waitFor(() => {
      expect(screen.queryByTestId('since-you-last-looked-button')).toBeNull()
    })
  })

  it('calls /agents with limit param', async () => {
    mockApi.get.mockResolvedValue({ agents: [] })
    renderComponent()
    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        expect.stringContaining('limit='),
      )
    })
  })

  it('button has correct tooltip text', async () => {
    mockApi.get.mockResolvedValue({
      agents: [
        { name: 'fresh', status: 'completed', completed_at: '2026-04-27T10:00:00Z' },
      ],
    })
    renderComponent()
    await waitFor(() => {
      const btn = screen.getByTestId('since-you-last-looked-button')
      expect(btn.getAttribute('title')).toBe('Agents finished while you were away')
    })
  })
})
