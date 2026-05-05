import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Adoption from './Adoption'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so TopBar (which uses a matchMedia breakpoint listener) does not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

// Route api.get calls by URL so TopBar's /notifications fetch doesn't
// consume the one-time adoption mock value (children effects fire first).
function mockAdoptionGet(adoptionData: typeof mockData | null, { fail = false } = {}) {
  mockedGet.mockImplementation((url: string) => {
    if (url === '/adoption/whats-working') {
      if (fail) return Promise.reject(new Error('network'))
      return Promise.resolve(adoptionData)
    }
    return Promise.resolve([]) // TopBar's /notifications and any other calls
  })
}

const mockData = {
  top_skills: [
    { id: 'builtin-builder', name: 'Builder', uses_this_week: 5, prev_week_uses: 4 },
    { id: 'builtin-research', name: 'Research', uses_this_week: 2, prev_week_uses: 0 },
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
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(2))

    const counts = screen.getAllByTestId('skill-use-count')
    expect(counts[0].textContent).toContain('5x')
    expect(counts[1].textContent).toContain('2x')
    expect(screen.getByText('Builder')).toBeTruthy()
    expect(screen.getByText('Research')).toBeTruthy()
  })

  it('renders recommendation cards with the why line', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getByTestId('recommendation-card')).toBeTruthy())

    const why = screen.getByTestId('rec-why')
    expect(why.textContent).toContain("you've been using Builder")
    expect(screen.getByText('Review')).toBeTruthy()
  })

  it('shows friendly empty state when there is no activity', async () => {
    mockAdoptionGet({ top_skills: [], recommendations: [], this_week: { agent_runs_completed: 0, top_spec_or_task: null } })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('empty-state')).toBeTruthy())
    expect(screen.queryByTestId('skill-card')).toBeNull()
  })

  it('shows agent run count and top task in the summary strip', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getByText(/7 agent runs finished/)).toBeTruthy())
    expect(screen.getByText(/Ship the adoption page/)).toBeTruthy()
  })

  it('empty state shows three starter cards with correct names', async () => {
    mockAdoptionGet({ top_skills: [], recommendations: [], this_week: { agent_runs_completed: 0, top_spec_or_task: null } })
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('starter-card')).toHaveLength(3))

    const cards = screen.getAllByTestId('starter-card')
    expect(cards[0].textContent).toContain('Builder')
    expect(cards[1].textContent).toContain('Brainstorm')
    expect(cards[2].textContent).toContain('Research')
    expect(screen.queryByTestId('skill-card')).toBeNull()
  })

  it('clicking a starter card navigates to /agents?template=builtin-<id>', async () => {
    mockAdoptionGet({ top_skills: [], recommendations: [], this_week: { agent_runs_completed: 0, top_spec_or_task: null } })
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('starter-card')).toHaveLength(3))

    const cards = screen.getAllByTestId('starter-card')
    expect(cards[0].tagName).toBe('BUTTON')
    fireEvent.click(cards[0])
    expect(mockNavigate).toHaveBeenCalledWith('/agents?template=builtin-builder')

    fireEvent.click(cards[1])
    expect(mockNavigate).toHaveBeenCalledWith('/agents?template=builtin-brainstorm')

    fireEvent.click(cards[2])
    expect(mockNavigate).toHaveBeenCalledWith('/agents?template=builtin-research')
  })

  it('skill-delta shows "new" when prev_week_uses is 0', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(2))

    const deltas = screen.getAllByTestId('skill-delta')
    // Builder: +25% (prev=4), Research: new (prev=0)
    const labels = deltas.map((el) => el.textContent)
    expect(labels).toContain('new')
  })

  it('skill-delta shows positive percent when uses_this_week > prev_week_uses', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(2))

    const deltas = screen.getAllByTestId('skill-delta')
    // Builder: 5 this / 4 prev → +25%
    expect(deltas[0].textContent).toBe('+25%')
    expect(deltas[0].className).toContain('text-green-400')
  })

  it('skill-delta shows negative percent when uses_this_week < prev_week_uses', async () => {
    mockAdoptionGet({ ...mockData, top_skills: [{ id: 'builtin-builder', name: 'Builder', uses_this_week: 3, prev_week_uses: 5 }] })
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(1))

    const delta = screen.getByTestId('skill-delta')
    expect(delta.textContent).toBe('-40%')
    expect(delta.className).toContain('text-red-400')
  })

  it('recommendation card is a button that navigates to agents with the template pre-selected', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getByTestId('recommendation-card')).toBeTruthy())

    const card = screen.getByTestId('recommendation-card')
    expect(card.tagName).toBe('BUTTON')
    fireEvent.click(card)
    expect(mockNavigate).toHaveBeenCalledWith('/agents?template=builtin-review')

    expect(screen.getByText('Review')).toBeTruthy()
    expect(screen.getByTestId('rec-why').textContent).toContain("you've been using Builder")
  })

  it('shows the page header while loading', () => {
    mockedGet.mockImplementation((url: string) => {
      if (url === '/adoption/whats-working') return new Promise(() => {})
      return Promise.resolve([])
    })
    renderPage()
    expect(screen.getByRole('banner')).toBeTruthy()
    expect(screen.getByText("What's working")).toBeTruthy()
  })

  it('shows the page header on error', async () => {
    mockAdoptionGet(null, { fail: true })
    renderPage()
    await waitFor(() => expect(screen.getByText(/Couldn't load/)).toBeTruthy())
    expect(screen.getByRole('banner')).toBeTruthy()
    expect(screen.getByText("What's working")).toBeTruthy()
  })

  it('shows the page header with data', async () => {
    mockAdoptionGet(mockData)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('skill-card')).toHaveLength(2))
    expect(screen.getByRole('banner')).toBeTruthy()
    expect(screen.getAllByText("What's working").length).toBeGreaterThan(0)
  })
})
