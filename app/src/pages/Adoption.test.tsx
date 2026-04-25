import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Adoption from './Adoption'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

const mockData = {
  top_skills: [
    { id: 'builtin-builder', name: 'Builder', uses_this_week: 5 },
    { id: 'builtin-research', name: 'Research', uses_this_week: 2 },
  ],
  recommendations: [
    { id: 'builtin-review', name: 'Review', why: "you've been using Builder" },
  ],
  this_week: {
    agent_runs_completed: 7,
    top_spec_or_task: 'Ship the adoption page',
  },
}

beforeEach(() => {
  vi.clearAllMocks()
})

function renderPage() {
  return render(
    <MemoryRouter>
      <Adoption />
    </MemoryRouter>
  )
}

describe('Adoption page', () => {
  it('renders skill cards with use counts', async () => {
    mockedGet.mockResolvedValueOnce(mockData)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(2))

    const counts = screen.getAllByTestId('skill-use-count')
    expect(counts[0].textContent).toContain('5x')
    expect(counts[1].textContent).toContain('2x')
    expect(screen.getByText('Builder')).toBeTruthy()
    expect(screen.getByText('Research')).toBeTruthy()
  })

  it('renders recommendation cards with the why line', async () => {
    mockedGet.mockResolvedValueOnce(mockData)
    renderPage()
    await waitFor(() => expect(screen.getByTestId('recommendation-card')).toBeTruthy())

    const why = screen.getByTestId('rec-why')
    expect(why.textContent).toContain("you've been using Builder")
    expect(screen.getByText('Review')).toBeTruthy()
  })

  it('shows friendly empty state when there is no activity', async () => {
    mockedGet.mockResolvedValueOnce({
      top_skills: [],
      recommendations: [],
      this_week: { agent_runs_completed: 0, top_spec_or_task: null },
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('empty-state')).toBeTruthy())
    expect(screen.queryByTestId('skill-card')).toBeNull()
  })

  it('shows agent run count and top task in the summary strip', async () => {
    mockedGet.mockResolvedValueOnce(mockData)
    renderPage()
    await waitFor(() => expect(screen.getByText(/7 agent runs finished/)).toBeTruthy())
    expect(screen.getByText(/Ship the adoption page/)).toBeTruthy()
  })
})
