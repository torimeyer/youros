import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PortfolioPage from './PortfolioPage'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
  },
}))

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
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
const mockedApiPut = vi.mocked(api.put)

const listsEmpty = { job_roles: [], pillars: [] }

// Route api.get by path: the page fetches both the rollup and the
// org theme list on load.
function mockGets(rollup: unknown, lists: unknown = listsEmpty) {
  mockedApiGet.mockImplementation((path: string) => {
    if (path === '/enterprise/lists') return Promise.resolve(lists)
    return Promise.resolve(rollup)
  })
}

const themedResponse = {
  themes: [
    {
      name: 'Growth',
      projects: [{ name: 'alpha', risk: 'quiet', last_modified: null }],
      tasks: [
        { id: '→1', title: 'Ship the signup flow', risk: 'overdue' },
        { id: '→2', title: 'Write the launch note', risk: 'none' },
      ],
      project_count: 1,
      task_count: 2,
      risk: 'overdue',
    },
    {
      name: null,
      projects: [],
      tasks: [{ id: '→9', title: 'Sort the inbox', risk: 'none' }],
      project_count: 0,
      task_count: 1,
      risk: 'none',
    },
  ],
  jira: { connected: false, tickets: [] },
}

const emptyResponse = {
  themes: [
    { name: null, projects: [], tasks: [], project_count: 0, task_count: 0, risk: 'none' },
  ],
  jira: { connected: false, tickets: [] },
}

const jiraResponse = {
  ...emptyResponse,
  jira: {
    connected: true,
    tickets: [
      {
        key: 'PROJ-7',
        summary: 'Fix the login flow',
        status: 'In Progress',
        priority: 'High',
        type: 'Bug',
        updated: '2026-07-08T09:00:00.000+0000',
        url: 'https://example.atlassian.net/browse/PROJ-7',
      },
    ],
  },
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PortfolioPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  mockedApiGet.mockReset()
  mockedApiPut.mockReset()
})

describe('PortfolioPage', () => {
  it('groups work under theme headings with plain risk words', async () => {
    mockedApiGet.mockResolvedValue(themedResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Growth')).toBeInTheDocument()
    })
    // Project and task rows inside the theme
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('Ship the signup flow')).toBeInTheDocument()
    expect(screen.getByText('Write the launch note')).toBeInTheDocument()
    // Risk flags in plain words (the bucket repeats its worst row's word,
    // so "overdue" and "quiet" can appear more than once)
    expect(screen.getAllByText('past due').length).toBeGreaterThan(0)
    expect(screen.getAllByText('no activity for a week').length).toBeGreaterThan(0)
    // Untagged work stays visible under the catch-all bucket
    expect(screen.getByText('No theme yet')).toBeInTheDocument()
    expect(screen.getByText('Sort the inbox')).toBeInTheDocument()
  })

  it('shows counts for each theme', async () => {
    mockedApiGet.mockResolvedValue(themedResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('1 project, 2 tasks')).toBeInTheDocument()
    })
  })

  it('shows a friendly explanation when nothing is themed yet', async () => {
    mockedApiGet.mockResolvedValue(emptyResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('portfolio-empty')).toBeInTheDocument()
    })
    expect(screen.getByText(/No themes yet/i)).toBeInTheDocument()
  })

  it('shows the Jira tickets section when connected', async () => {
    mockedApiGet.mockResolvedValue(jiraResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Your Jira tickets')).toBeInTheDocument()
    })
    expect(screen.getByText('PROJ-7')).toBeInTheDocument()
    expect(screen.getByText('Fix the login flow')).toBeInTheDocument()
  })

  it('hides the Jira section when not connected', async () => {
    mockedApiGet.mockResolvedValue(themedResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Growth')).toBeInTheDocument()
    })
    expect(screen.queryByText('Your Jira tickets')).not.toBeInTheDocument()
  })

  it('shows an error message when the request fails', async () => {
    mockedApiGet.mockRejectedValue(new Error('boom'))
    renderPage()

    await waitFor(() => {
      expect(
        screen.getByText(/Could not load your portfolio/i)
      ).toBeInTheDocument()
    })
  })

  it('shows the theme setup card with plain copy when no themes exist', async () => {
    mockGets(emptyResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('theme-setup-card')).toBeInTheDocument()
    })
    expect(screen.getByText('Set up your themes')).toBeInTheDocument()
    expect(
      screen.getByText(/Themes are the big goals your work supports/i)
    ).toBeInTheDocument()
    expect(screen.getByTestId('theme-input')).toBeInTheDocument()
    expect(screen.getByTestId('theme-add')).toBeInTheDocument()
  })

  it('adds a theme: typing a name and clicking Add saves it', async () => {
    mockGets(themedResponse, { job_roles: ['PM'], pillars: ['Growth'] })
    mockedApiPut.mockResolvedValue({ pillars: ['Growth', 'Customer trust'] })
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('theme-input')).toBeInTheDocument()
    })
    // With themes already saved, the card shows the short label and a
    // removable chip instead of the setup copy.
    expect(screen.getByText('Your themes')).toBeInTheDocument()
    expect(screen.queryByText('Set up your themes')).not.toBeInTheDocument()
    expect(screen.getByTestId('theme-remove-Growth')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('theme-input'), {
      target: { value: 'Customer trust' },
    })
    fireEvent.click(screen.getByTestId('theme-add'))

    await waitFor(() => {
      expect(mockedApiPut).toHaveBeenCalledWith('/enterprise/lists/pillars', {
        values: ['Growth', 'Customer trust'],
      })
    })
  })

  it('does not mark open tasks with a done checkmark icon', async () => {
    mockGets(themedResponse)
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Growth')).toBeInTheDocument()
    })
    // Icon renders its name as the element text; check_circle is the
    // green "looks done" icon and must not appear on open task rows.
    expect(screen.queryByText('check_circle')).not.toBeInTheDocument()
  })
})
