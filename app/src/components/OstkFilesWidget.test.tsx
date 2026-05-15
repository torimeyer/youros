import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import OstkFilesWidget from './OstkFilesWidget'

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

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => vi.fn() }
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

function renderWidget() {
  return render(
    <MemoryRouter>
      <OstkFilesWidget />
    </MemoryRouter>
  )
}

describe('OstkFilesWidget', () => {
  beforeEach(async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/needles')) return Promise.resolve({ needles: [] })
      if (url.includes('/files/audit')) return Promise.resolve({ events: [] })
      return Promise.resolve({})
    })
  })

  it('renders Tasks and Audit buttons', async () => {
    renderWidget()
    await waitFor(() => {
      expect(screen.getByTestId('ostk-tasks-button')).toBeDefined()
      expect(screen.getByTestId('ostk-audit-button')).toBeDefined()
    })
  })

  it('labels the needle button "Tasks" not "Needles" (→1364)', async () => {
    renderWidget()
    await waitFor(() => {
      const label = screen.getByTestId('ostk-tasks-label')
      expect(label.textContent).toContain('Tasks')
      expect(label.textContent).not.toContain('Needles')
    })
  })

  it('pre-fetches counts on mount so badges are not stuck at 0 (→1364)', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/files/needles'))
        return Promise.resolve({ needles: [{ id: '1' }, { id: '2' }] })
      if (url.includes('/files/audit'))
        return Promise.resolve({ events: [{ event: 'a' }, { event: 'b' }, { event: 'c' }] })
      return Promise.resolve({})
    })

    renderWidget()

    await waitFor(() => {
      expect(screen.getByTestId('ostk-tasks-label').textContent).toContain('(2)')
      expect(screen.getByTestId('ostk-audit-label').textContent).toContain('(3)')
    })
  })

  it('calls both /files/needles and /files/audit on mount', async () => {
    const { api } = await import('../lib/api')
    const mockGet = api.get as ReturnType<typeof vi.fn>

    renderWidget()

    await waitFor(() => {
      const calls = mockGet.mock.calls.map((c: unknown[]) => c[0])
      expect(calls.some((u: unknown) => String(u).includes('/files/needles'))).toBe(true)
      expect(calls.some((u: unknown) => String(u).includes('/files/audit'))).toBe(true)
    })
  })
})
