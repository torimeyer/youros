import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Confluence from './Confluence'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

function renderConfluence(initialPath = '/confluence') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/confluence" element={<Confluence />} />
        <Route path="/confluence/:pageId" element={<Confluence />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Confluence page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders ConnectCard when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.getByText('Connect Atlassian (Jira + Confluence)')).toBeInTheDocument()
  })

  it('shows connect form inputs when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('confluence-page')).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText('yourco.atlassian.net')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('ATATT3x...')).toBeInTheDocument()
  })

  it('renders page list when connected', async () => {
    const pages = [
      {
        id: '99001',
        title: 'IAM Strategy 2026',
        space_id: 'IAM',
        updated: '2026-04-28T00:00:00Z',
        url: 'https://example.atlassian.net/wiki/spaces/IAM/pages/99001',
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/confluence/pages')) {
        return Promise.resolve({ pages })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('confluence-row-99001')).toBeInTheDocument()
    })
    expect(screen.getByText('IAM Strategy 2026')).toBeInTheDocument()
  })

  it('renders page detail when pageId param is present', async () => {
    const pageDetail = {
      id: '99001',
      title: 'IAM Strategy 2026',
      space_id: 'IAM',
      body_html: '<h1>IAM Strategy</h1><p>Content goes here.</p>',
      created: '2026-01-01T00:00:00Z',
      updated: '2026-04-28T00:00:00Z',
      url: 'https://example.atlassian.net/wiki/spaces/IAM/pages/99001',
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/confluence/page/99001')) {
        return Promise.resolve(pageDetail)
      }
      return Promise.resolve({})
    })

    renderConfluence('/confluence/99001')

    await waitFor(() => {
      expect(screen.getByText('IAM Strategy 2026')).toBeInTheDocument()
    })
    expect(screen.getByTestId('confluence-page')).toBeInTheDocument()
  })

  it('pre-fills site URL input from /atlassian/defaults', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: 'https://company.atlassian.net' })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      const siteInput = screen.getByPlaceholderText('yourco.atlassian.net') as HTMLInputElement
      expect(siteInput.value).toBe('https://company.atlassian.net')
    })
  })

  it('renders empty state when no pages', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/confluence/pages')) {
        return Promise.resolve({ pages: [] })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByText('No recent pages')).toBeInTheDocument()
    })
  })

  it('shows "Connect Atlassian (Jira + Confluence)" title when oauth is available', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByText('Connect Atlassian (Jira + Confluence)')).toBeInTheDocument()
    })
  })

  it('shows jira_url and confluence_url subtext when connected with both URLs', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({
          connected: true,
          email: 'user@example.com',
          site: 'example.atlassian.net',
          jira_url: 'https://example.atlassian.net/jira',
          confluence_url: 'https://example.atlassian.net/wiki',
        })
      }
      if (path.includes('/atlassian/confluence/pages')) {
        return Promise.resolve({ pages: [] })
      }
      return Promise.resolve({})
    })

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('confluence-jira-url')).toBeInTheDocument()
      expect(screen.getByTestId('confluence-confluence-url')).toBeInTheDocument()
    })
    expect(screen.getByTestId('confluence-jira-url').textContent).toBe('https://example.atlassian.net/jira')
    expect(screen.getByTestId('confluence-confluence-url').textContent).toBe('https://example.atlassian.net/wiki')
  })

  it('forceTokenForm is false at mount even after a prior mount-cycle set it true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      return Promise.resolve({})
    })

    const { unmount } = renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('confluence-oauth-connect')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByTestId('confluence-use-token'))
    })

    expect(screen.getByPlaceholderText('yourco.atlassian.net')).toBeInTheDocument()

    unmount()

    renderConfluence()

    await waitFor(() => {
      expect(screen.getByTestId('confluence-oauth-connect')).toBeInTheDocument()
    })
  })
})
