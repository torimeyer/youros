import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AboutMyOS from './AboutMyOS'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

const mockedGet = vi.mocked(api.get)

function mockApiResponses({
  version = '1.2.3',
  taskCount = 0,
  agentCount = 0,
  decisionCount = 0,
}: { version?: string; taskCount?: number; agentCount?: number; decisionCount?: number } = {}) {
  mockedGet.mockImplementation((path: string) => {
    if (path === '/upgrade/status') {
      return Promise.resolve({ myos: { current: version } } as never)
    }
    if (path === '/tasks') {
      return Promise.resolve({ tasks: Array.from({ length: taskCount }, () => ({})) } as never)
    }
    if (path === '/agents') {
      return Promise.resolve({ agents: Array.from({ length: agentCount }, () => ({})) } as never)
    }
    if (path === '/decisions') {
      return Promise.resolve({ decisions: [], count: decisionCount } as never)
    }
    return Promise.resolve({} as never)
  })
}

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
})

function renderPage() {
  return render(
    <MemoryRouter>
      <AboutMyOS />
    </MemoryRouter>
  )
}

describe('AboutMyOS page', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockApiResponses()
  })

  it('renders all sections including the new "why-not-wrapper" section', () => {
    renderPage()
    const sectionIds = [
      'what-myos-is',
      'why-not-wrapper',
      'kernel-underneath',
      'how-agents-coordinate',
      'work-that-compounds',
      'local-and-model-agnostic',
      'where-to-look-next',
    ]
    for (const id of sectionIds) {
      expect(screen.getByTestId(`about-section-${id}`)).toBeTruthy()
      expect(screen.getByTestId(`about-heading-${id}`)).toBeTruthy()
    }
  })

  it('renders hero with myOS title and tagline', () => {
    renderPage()
    expect(screen.getByTestId('about-hero')).toBeTruthy()
    expect(screen.getByTestId('about-hero').textContent).toMatch(/myOS/)
  })

  it('credits Scott Walters as ostk creator', () => {
    renderPage()
    const section = screen.getByTestId('about-section-kernel-underneath')
    expect(section.textContent).toMatch(/Scott Walters/i)
  })

  it('points the repo link to the real github URL', () => {
    renderPage()
    const repoLink = screen.getByText(/Project repository/i).closest('a')
    expect(repoLink?.getAttribute('href')).toBe('https://github.com/torimeyer/myos')
  })

  it('does not show the stale "Updated: April 2026" text', () => {
    renderPage()
    expect(screen.queryByText(/Updated: April 2026/)).toBeNull()
  })

  it('shows the version line at the bottom from /upgrade/status', async () => {
    mockApiResponses({ version: '4.2.5' })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('about-version').textContent).toMatch(/4\.2\.5/)
    })
  })

  it('renders live counters with real numbers when counts are non-zero', async () => {
    mockApiResponses({ taskCount: 42, agentCount: 17, decisionCount: 9 })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('about-counters')).toBeTruthy()
    })
    const counters = screen.getByTestId('about-counters')
    expect(counters.textContent).toMatch(/42/)
    expect(counters.textContent).toMatch(/17/)
    expect(counters.textContent).toMatch(/9/)
  })

  it('hides the counters block when all three counts are zero', async () => {
    mockApiResponses({ taskCount: 0, agentCount: 0, decisionCount: 0 })
    renderPage()
    // Wait for the API calls to settle, then assert the block is absent.
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/tasks')
    })
    expect(screen.queryByTestId('about-counters')).toBeNull()
  })

  it('includes a Keyboard shortcuts link in "where to look next"', () => {
    renderPage()
    const section = screen.getByTestId('about-section-where-to-look-next')
    expect(section.textContent).toMatch(/Keyboard shortcuts/i)
  })

  it('renders section headings with correct text', () => {
    renderPage()
    expect(screen.getByTestId('about-heading-what-myos-is').textContent).toContain('What myOS is')
    expect(screen.getByTestId('about-heading-kernel-underneath').textContent).toContain(
      'kernel underneath'
    )
    expect(screen.getByTestId('about-heading-how-agents-coordinate').textContent).toContain(
      'How agents coordinate'
    )
    expect(screen.getByTestId('about-heading-work-that-compounds').textContent).toContain(
      'Work that compounds'
    )
    expect(screen.getByTestId('about-heading-local-and-model-agnostic').textContent).toContain(
      'Local first'
    )
    expect(screen.getByTestId('about-heading-where-to-look-next').textContent).toContain(
      'Where to look next'
    )
  })
})
