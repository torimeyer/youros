import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PortfolioPage from './PortfolioPage'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
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
    expect(screen.getAllByText('overdue').length).toBeGreaterThan(0)
    expect(screen.getAllByText('quiet').length).toBeGreaterThan(0)
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
})
