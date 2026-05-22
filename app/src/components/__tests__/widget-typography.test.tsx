import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Mock api for all widgets
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  }
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

import { api } from '../../lib/api'
import ExecUpdateWidget from '../ExecUpdateWidget'
import ActiveAgentsWidget from '../ActiveAgentsWidget'
import NeedlesCountWidget from '../NeedlesCountWidget'
import BlockersWidget from '../BlockersWidget'

const mockedGet = vi.mocked(api.get)

// Canonical scale: all widget card titles must use text-lg font-semibold on an h2

describe('widget typography — canonical scale', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── ExecUpdateWidget ──────────────────────────────────────────────────────
  // This widget drifted to text-sm on an h3. Tests below are the RED signal.
  describe('ExecUpdateWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ drafts: [] })
      const { container } = render(<ExecUpdateWidget />)
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })

    it('error state uses text-sm body text', async () => {
      mockedGet.mockRejectedValue(new Error('fail'))
      render(<ExecUpdateWidget />)
      await waitFor(() => {
        const el = screen.getByTestId('exec-update-error')
        expect(el.classList.contains('text-sm')).toBe(true)
      })
    })

    it('empty state primary text uses text-sm', async () => {
      mockedGet.mockResolvedValue({ drafts: [] })
      render(<ExecUpdateWidget />)
      await waitFor(() => {
        const empty = screen.getByTestId('exec-update-empty')
        // First <p> inside empty state is the primary message
        const primaryP = empty.querySelector('p')
        expect(primaryP).not.toBeNull()
        expect(primaryP!.classList.contains('text-sm')).toBe(true)
      })
    })
  })

  // ── ActiveAgentsWidget (running-agents) ───────────────────────────────────
  describe('ActiveAgentsWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ agents: [] })
      const { container } = render(
        <MemoryRouter><ActiveAgentsWidget /></MemoryRouter>
      )
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })

  // ── NeedlesCountWidget (active-tasks/open needles) ────────────────────────
  describe('NeedlesCountWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ counts: { open: 0, p0: 0, p1: 0 } })
      const { container } = render(
        <MemoryRouter><NeedlesCountWidget /></MemoryRouter>
      )
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })

  // ── BlockersWidget ────────────────────────────────────────────────────────
  describe('BlockersWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ blockers: [] })
      const { container } = render(<BlockersWidget />)
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })
})
