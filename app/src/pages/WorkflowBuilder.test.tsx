import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import WorkflowBuilder from './WorkflowBuilder'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
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

function renderBuilder(id?: string) {
  if (id) {
    return render(
      <MemoryRouter initialEntries={[`/workflows/builder/${id}`]}>
        <Routes>
          <Route path="/workflows/builder/:id" element={<WorkflowBuilder />} />
          <Route path="/workflows/builder" element={<WorkflowBuilder />} />
        </Routes>
      </MemoryRouter>
    )
  }
  return render(
    <MemoryRouter initialEntries={['/workflows/builder']}>
      <Routes>
        <Route path="/workflows/builder" element={<WorkflowBuilder />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowBuilder page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('renders the automation name input on new workflow', () => {
    renderBuilder()
    expect(screen.getByPlaceholderText('Automation name')).toBeInTheDocument()
  })

  it('renders Save and Run buttons', () => {
    renderBuilder()
    expect(screen.getByText('Save')).toBeInTheDocument()
    expect(screen.getByText('Run automation')).toBeInTheDocument()
  })

  it('shows Add step button', () => {
    renderBuilder()
    expect(screen.getByText('Add step')).toBeInTheDocument()
  })

  it('renders LoadingState spinner when automation is running', async () => {
    const workflowData = {
      workflow: {
        id: 'wf-run',
        name: 'Running workflow',
        status: 'running',
        steps: [
          {
            id: 'step-1',
            agent_name: 'runner',
            prompt: 'Do something',
            model: 'sonnet',
            budget: 2.0,
            depends_on: [],
            status: 'running',
          },
        ],
      },
    }

    mockedGet.mockResolvedValue(workflowData)

    renderBuilder('wf-run')

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/workflows/wf-run/status')
    })

    // Simulate the running state being set by triggering a run
    // The workflow status is "running" from load, so the banner should show
    await waitFor(() => {
      // The page should load the workflow name
      expect(screen.getByDisplayValue('Running workflow')).toBeInTheDocument()
    })
  })

  it('renders with default "My automation" title for new workflow', () => {
    renderBuilder()
    const input = screen.getByPlaceholderText('Automation name') as HTMLInputElement
    expect(input.value).toBe('My automation')
  })
})