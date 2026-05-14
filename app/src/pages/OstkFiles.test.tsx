import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import OstkFiles from './OstkFiles'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  }
})

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

function renderPage() {
  return render(
    <MemoryRouter>
      <OstkFiles />
    </MemoryRouter>
  )
}

describe('OstkFiles', () => {
  beforeEach(async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/decisions')) return Promise.resolve({ decisions: [] })
      if (url.includes('/files/needles')) return Promise.resolve({ needles: [] })
      if (url.includes('/files/audit')) return Promise.resolve({ events: [] })
      return Promise.resolve({})
    })
  })

  it('renders all three tab buttons', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('tab-decisions')).toBeDefined()
      expect(screen.getByTestId('tab-needles')).toBeDefined()
      expect(screen.getByTestId('tab-audit')).toBeDefined()
    })
  })

  it('shows Decisions tab as active by default', async () => {
    renderPage()
    await waitFor(() => {
      const decisionsTab = screen.getByTestId('tab-decisions')
      expect(decisionsTab.className).toContain('bg-slate-700')
    })
  })

  it('renders the search input', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-search')).toBeDefined()
    })
  })

  it('renders decision cards when data is returned', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/decisions')) {
        return Promise.resolve({
          decisions: [
            {
              name: 'my-decision.md',
              title: 'My decision title',
              modified_at: new Date().toISOString(),
              size: 100,
              content: '# My decision title\nBody text.',
            },
          ],
        })
      }
      return Promise.resolve({ needles: [], events: [] })
    })

    renderPage()
    await waitFor(() => {
      expect(screen.getAllByTestId('decision-card').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('My decision title')).toBeDefined()
  })

  it('renders needle cards on needles tab', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/needles')) {
        return Promise.resolve({
          needles: [
            { id: '→42', title: 'Fix the thing', status: 'open', priority: 'P1', created_at: new Date().toISOString() },
          ],
        })
      }
      return Promise.resolve({ decisions: [], events: [] })
    })

    renderPage()
    const needlesTab = await screen.findByTestId('tab-needles')
    needlesTab.click()

    await waitFor(() => {
      expect(screen.getAllByTestId('needle-card').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('Fix the thing')).toBeDefined()
  })

  it('renders audit cards on audit tab', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/audit')) {
        return Promise.resolve({
          events: [
            { event: 'hook.session.start', ts: new Date().toISOString() },
          ],
        })
      }
      return Promise.resolve({ decisions: [], needles: [] })
    })

    renderPage()
    const auditTab = await screen.findByTestId('tab-audit')
    auditTab.click()

    await waitFor(() => {
      expect(screen.getAllByTestId('audit-card').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('hook.session.start')).toBeDefined()
  })

  it('shows empty state when no decisions', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('decisions-list')).toBeDefined()
    })
    expect(screen.getByText('No decision docs found in .ostk/.')).toBeDefined()
  })

  // Regression guard for →1353: tab counts must be correct on first paint
  // without clicking each tab. Before the fix, Tasks and Audit tabs showed
  // (0) until the user clicked them, because only the initial decisions tab
  // was fetched on mount.
  it('shows correct tab counts for Tasks and Audit without clicking those tabs (→1353)', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/decisions')) return Promise.resolve({ decisions: [] })
      if (url.includes('/files/needles')) return Promise.resolve({
        needles: [
          { id: '→1', title: 'Task one', status: 'open', priority: 'P1', created_at: new Date().toISOString() },
          { id: '→2', title: 'Task two', status: 'open', priority: 'P2', created_at: new Date().toISOString() },
        ],
      })
      if (url.includes('/files/audit')) return Promise.resolve({
        events: [
          { event: 'hook.session.start', ts: new Date().toISOString() },
          { event: 'hook.session.end', ts: new Date().toISOString() },
          { event: 'tool.call', ts: new Date().toISOString() },
        ],
      })
      return Promise.resolve({})
    })

    renderPage()

    // Wait for prefetch to land — Tasks tab should show (2), Audit should show (3)
    // without any click interaction.
    await waitFor(() => {
      const tasksTab = screen.getByTestId('tab-needles')
      expect(tasksTab.textContent).toContain('(2)')
    })
    await waitFor(() => {
      const auditTab = screen.getByTestId('tab-audit')
      expect(auditTab.textContent).toContain('(3)')
    })
  })
})
