import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
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
import BlockersWidget from '../BlockersWidget'
import RecentSpecsWidget from '../RecentSpecsWidget'
import CompetitiveIntelWidget from '../CompetitiveIntelWidget'
import JiraWidget from '../JiraWidget'

const mockedGet = vi.mocked(api.get)

// Canonical scale: all widget card titles must use text-lg font-semibold on an h2

describe('widget typography — canonical scale', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  // ── RecentSpecsWidget ─────────────────────────────────────────────────────
  describe('RecentSpecsWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ docs: [] })
      const { container } = render(
        <MemoryRouter><RecentSpecsWidget /></MemoryRouter>
      )
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })

  // ── CompetitiveIntelWidget ────────────────────────────────────────────────
  describe('CompetitiveIntelWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ captures: [] })
      const { container } = render(<CompetitiveIntelWidget />)
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })

  // ── JiraWidget ────────────────────────────────────────────────────────────
  describe('JiraWidget', () => {
    it('title is an h2 with text-lg font-semibold', async () => {
      mockedGet.mockResolvedValue({ issues: [] })
      const { container } = render(
        <MemoryRouter><JiraWidget /></MemoryRouter>
      )
      await waitFor(() => {
        const heading = container.querySelector('h2')
        expect(heading).not.toBeNull()
        expect(heading!.classList.contains('text-lg')).toBe(true)
        expect(heading!.classList.contains('font-semibold')).toBe(true)
      })
    })
  })
})
