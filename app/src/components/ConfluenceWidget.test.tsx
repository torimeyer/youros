import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ConfluenceWidget from './ConfluenceWidget'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
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

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

function renderWidget() {
  return render(
    <MemoryRouter>
      <ConfluenceWidget />
    </MemoryRouter>
  )
}

describe('ConfluenceWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('omits space_key when default_confluence_space is empty', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ default_confluence_space: '' })
      if (path.includes('/atlassian/confluence/pages')) return Promise.resolve({ pages: [] })
      return Promise.resolve({})
    })

    renderWidget()

    await waitFor(() => {
      const calls = mockedGet.mock.calls.map(([p]) => p as string)
      const pagesCall = calls.find((p) => p.includes('confluence/pages'))
      expect(pagesCall).toBeTruthy()
      expect(pagesCall).not.toContain('space_key=')
    })
  })

  it('appends space_key when default_confluence_space is set', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ default_confluence_space: 'IAM' })
      if (path.includes('/atlassian/confluence/pages')) return Promise.resolve({ pages: [] })
      return Promise.resolve({})
    })

    renderWidget()

    await waitFor(() => {
      const calls = mockedGet.mock.calls.map(([p]) => p as string)
      const pagesCall = calls.find((p) => p.includes('confluence/pages'))
      expect(pagesCall).toContain('space_key=IAM')
    })
  })

  it('renders page titles when pages are returned', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ default_confluence_space: '' })
      if (path.includes('/atlassian/confluence/pages')) {
        return Promise.resolve({ pages: [{ id: '1', title: 'My Page', space_id: 'IAM', updated: '', url: '' }] })
      }
      return Promise.resolve({})
    })

    renderWidget()

    await waitFor(() => {
      expect(screen.getByText('My Page')).toBeInTheDocument()
    })
  })
})
