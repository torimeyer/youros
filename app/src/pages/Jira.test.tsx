import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Jira from './Jira'

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

function renderJira(initialPath = '/jira') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/jira" element={<Jira />} />
        <Route path="/jira/:key" element={<Jira />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Jira page', () => {
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

    renderJira()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })
    expect(screen.getByText('Connect Atlassian')).toBeInTheDocument()
  })

  it('shows connect form with three inputs when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false, email: '', site: '' })
      }
      return Promise.resolve({})
    })

    renderJira()

    await waitFor(() => {
      expect(screen.getByTestId('jira-connect-form')).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText('yourco.atlassian.net')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('ATATT3x...')).toBeInTheDocument()
  })

  it('renders issue list when connected', async () => {
    const issues = [
      {
        key: 'PROJ-42',
        summary: 'Fix the login flow',
        status: 'In Progress',
        priority: 'High',
        type: 'Bug',
        updated: '2026-04-29T00:00:00Z',
        url: 'https://example.atlassian.net/browse/PROJ-42',
      },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/jira/issues')) {
        return Promise.resolve({ issues })
      }
      return Promise.resolve({})
    })

    renderJira()

    await waitFor(() => {
      expect(screen.getByTestId('jira-issue-row-PROJ-42')).toBeInTheDocument()
    })
    expect(screen.getByText('Fix the login flow')).toBeInTheDocument()
    expect(screen.getByText('PROJ-42')).toBeInTheDocument()
  })

  it('shows disconnect button when connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/jira/issues')) {
        return Promise.resolve({ issues: [] })
      }
      return Promise.resolve({})
    })

    renderJira()

    await waitFor(() => {
      expect(screen.getByTestId('jira-disconnect')).toBeInTheDocument()
    })
  })

  it('renders issue detail view when key param is present', async () => {
    const detail = {
      key: 'PROJ-42',
      summary: 'Fix the login flow',
      description_html: '<p>Details about the bug</p>',
      status: 'In Progress',
      priority: 'High',
      type: 'Bug',
      assignee: 'Tori Meyer',
      reporter: 'Alice',
      created: '2026-01-01T00:00:00Z',
      updated: '2026-04-29T00:00:00Z',
      url: 'https://example.atlassian.net/browse/PROJ-42',
      comments: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/jira/issue/PROJ-42')) {
        return Promise.resolve(detail)
      }
      return Promise.resolve({})
    })

    renderJira('/jira/PROJ-42')

    await waitFor(() => {
      expect(screen.getByText('Fix the login flow')).toBeInTheDocument()
    })
    expect(screen.getByTestId('jira-page')).toBeInTheDocument()
  })

  it('renders empty state when no issues assigned', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/jira/issues')) {
        return Promise.resolve({ issues: [] })
      }
      return Promise.resolve({})
    })

    renderJira()

    await waitFor(() => {
      expect(screen.getByText('No issues assigned to you')).toBeInTheDocument()
    })
  })
})
