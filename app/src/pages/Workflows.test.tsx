import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Workflows from './Workflows'

// Mock the api module so we can inspect calls and simulate responses.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

// jsdom does not provide window.matchMedia. Stub it so components that
// use responsive breakpoints do not crash.
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

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

const standupWorkflow = {
  id: 'wf-standup',
  name: 'Daily standup',
  status: 'pending' as const,
  created_at: '2026-04-14T00:00:00Z',
  steps: [
    {
      id: 'step-1',
      agent_name: 'gather',
      prompt: 'List closed tasks',
      model: 'sonnet',
      budget: 2.0,
      depends_on: [],
      status: 'pending' as const,
    },
  ],
}

function primeMocks() {
  mockedGet.mockImplementation((path: string) => {
    if (path === '/workflows') {
      return Promise.resolve({ workflows: [standupWorkflow] })
    }
    if (path === '/workflows/templates') {
      return Promise.resolve({ templates: [] })
    }
    return Promise.resolve({})
  })
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Workflows />
    </MemoryRouter>,
  )
}

describe('Workflows page: run history drawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    primeMocks()
  })

  it('shows run history when the history button is clicked', async () => {
    // Seed the runs API with one completed run
    mockedGet.mockImplementation((path: string) => {
      if (path === '/workflows') {
        return Promise.resolve({ workflows: [standupWorkflow] })
      }
      if (path === '/workflows/templates') {
        return Promise.resolve({ templates: [] })
      }
      if (path === `/workflows/${standupWorkflow.id}/runs`) {
        return Promise.resolve({
          runs: [
            {
              run_id: 'run-abc',
              status: 'done',
              started_at: '2026-04-14T10:00:00Z',
              completed_at: '2026-04-14T10:00:45Z',
              duration_seconds: 45,
              steps: [
                {
                  id: 'step-1',
                  agent_name: 'gather',
                  status: 'done',
                  started_at: '2026-04-14T10:00:00Z',
                  finished_at: '2026-04-14T10:00:45Z',
                },
              ],
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderPage()

    // Wait for the workflow card to appear
    await waitFor(() => {
      expect(screen.getByText('Daily standup')).toBeInTheDocument()
    })

    // Click the history button (title="Show run history")
    const historyBtn = screen.getByTitle('Show run history')
    fireEvent.click(historyBtn)

    // The runs drawer should load and show the run result
    await waitFor(() => {
      expect(screen.getByText('Recent runs')).toBeInTheDocument()
    })

    // Status badge should show Success
    expect(screen.getByText('Success')).toBeInTheDocument()

    // Duration should be shown
    expect(screen.getByText('45s')).toBeInTheDocument()

    // Expand the run row to see per-step details
    const successBtn = screen.getByText('Success').closest('button')
    if (successBtn) fireEvent.click(successBtn)

    // The step agent name should link to the Agents page
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /gather/i })).toBeInTheDocument()
    })
  })
})

describe('Workflows page: handleUseTemplate slug regression', () => {
  // Regression test: template step names must be slugified into clean agent
  // identifiers before being stored. A step named "Closed this week" must
  // produce agent_name "closed-this-week", NOT the raw display label.
  it('slugifies template step names so the agent name has no spaces or capitals', async () => {
    const weeklyReviewTemplate = {
      id: 'builtin-weekly-review',
      name: 'Weekly review',
      description: 'Weekly review automation',
      icon: 'rate_review',
      steps: [
        { name: 'Closed this week', prompt: 'Pull closed tasks.' },
        { name: 'Still open', prompt: 'Pull open tasks.' },
        { name: 'Write the review', prompt: 'Write a weekly review.' },
      ],
    }

    mockedGet.mockImplementation((path: string) => {
      if (path === '/workflows') return Promise.resolve({ workflows: [] })
      if (path === '/workflows/templates')
        return Promise.resolve({ templates: [weeklyReviewTemplate] })
      return Promise.resolve({})
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Weekly review')).toBeInTheDocument()
    })

    // Clicking "Use" should store a prefill in localStorage with slugified agent names
    const useBtn = screen.getByRole('button', { name: /^Use$/i })
    fireEvent.click(useBtn)

    const stored = localStorage.getItem('automation-template-prefill')
    expect(stored).not.toBeNull()
    const prefill = JSON.parse(stored!)
    const agentNames: string[] = prefill.steps.map((s: { agent_name: string }) => s.agent_name)

    // Each agent name must be lowercase, no spaces, no capitals
    agentNames.forEach((n) => {
      expect(n).toMatch(/^[a-z0-9-]+$/)
    })

    expect(agentNames[0]).toBe('closed-this-week')
    expect(agentNames[1]).toBe('still-open')
    expect(agentNames[2]).toBe('write-the-review')
  })
})

describe('Workflows page Run handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    primeMocks()
  })

  it('shows an immediate success banner when Run is clicked on a workflow card', async () => {
    mockedPost.mockResolvedValue({ result: 'started', workflow: standupWorkflow })

    renderPage()

    // Wait for the workflow to render in the list.
    await waitFor(() => {
      expect(screen.getByText('Daily standup')).toBeInTheDocument()
    })

    // The play_arrow button has title="Run automation".
    const runButton = screen.getByTitle('Run automation')
    fireEvent.click(runButton)

    // The run endpoint must be called with the workflow id.
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/workflows/wf-standup/run')
    })

    // And the user must see immediate feedback, not silent success.
    await waitFor(() => {
      expect(
        screen.getByText(/Automation started\. Steps will update as they finish\./i),
      ).toBeInTheDocument()
    })
  })

  it('surfaces an error banner when the Run POST fails', async () => {
    mockedPost.mockRejectedValue(new Error('Backend is down'))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Daily standup')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Run automation'))

    await waitFor(() => {
      expect(screen.getByText(/Backend is down/i)).toBeInTheDocument()
    })
  })

  it('status error renders ErrorBanner with data-testid', async () => {
    mockedPost.mockRejectedValue(new Error('Service unavailable'))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Daily standup')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Run automation'))

    await waitFor(() => {
      expect(screen.getByTestId('error-banner')).toBeInTheDocument()
    })
  })
})
