import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ConfluenceWidget from './ConfluenceWidget'

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
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
const mockedPost = vi.mocked(api.post)

function localDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d)
  r.setDate(r.getDate() + n)
  return r
}

const TODAY = new Date()
const TASK_TODAY = { id: 't1', text: 'Fix bug', due: localDateStr(TODAY), page_id: 'p1', url: '' }
const TASK_TOMORROW = { id: 't2', text: 'Write docs', due: localDateStr(addDays(TODAY, 1)), page_id: 'p2', url: '' }
const TASK_IN_3 = { id: 't3', text: 'Review PR', due: localDateStr(addDays(TODAY, 3)), page_id: 'p3', url: '' }
const TASK_OVERDUE = { id: 't4', text: 'Old task', due: localDateStr(addDays(TODAY, -2)), page_id: 'p4', url: '' }
const MENTION_ROW = { id: 'm1', title: 'Team sync notes', type: 'page', updated: '2026-07-10', url: '' }

function mockBothOk(tasks = [TASK_TODAY], rows = [MENTION_ROW]) {
  mockedGet.mockImplementation((path: string) => {
    if (path.includes('/atlassian/confluence/my-tasks')) return Promise.resolve({ tasks })
    if (path.includes('/atlassian/confluence/query')) return Promise.resolve({ rows })
    return Promise.reject(new Error(`unexpected path: ${path}`))
  })
}

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
    mockedPost.mockResolvedValue({ ok: true })
  })

  it('keeps data-testid="widget-confluence"', () => {
    mockBothOk([], [])
    renderWidget()
    expect(screen.getByTestId('widget-confluence')).toBeInTheDocument()
  })

  it('shows Confluence header title', async () => {
    mockBothOk([], [])
    renderWidget()
    await waitFor(() => expect(screen.getByText('Confluence')).toBeInTheDocument())
  })

  it('shows badge with action item count when tasks exist', async () => {
    mockBothOk([TASK_TODAY, TASK_TOMORROW])
    renderWidget()
    await waitFor(() => expect(screen.getByText('2 action items')).toBeInTheDocument())
  })

  it('hides badge when no tasks', async () => {
    mockBothOk([])
    renderWidget()
    await waitFor(() => expect(screen.queryByText(/action items/)).toBeNull())
  })

  it('renders action item text', async () => {
    mockBothOk([TASK_TODAY])
    renderWidget()
    await waitFor(() => expect(screen.getByText('Fix bug')).toBeInTheDocument())
  })

  it('shows "due today" label', async () => {
    mockBothOk([TASK_TODAY])
    renderWidget()
    await waitFor(() => expect(screen.getByText('due today')).toBeInTheDocument())
  })

  it('shows "due tomorrow" label', async () => {
    mockBothOk([TASK_TOMORROW])
    renderWidget()
    await waitFor(() => expect(screen.getByText('due tomorrow')).toBeInTheDocument())
  })

  it('shows "due in N days" label', async () => {
    mockBothOk([TASK_IN_3])
    renderWidget()
    await waitFor(() => expect(screen.getByText('due in 3 days')).toBeInTheDocument())
  })

  it('shows "overdue N days" label', async () => {
    mockBothOk([TASK_OVERDUE])
    renderWidget()
    await waitFor(() => expect(screen.getByText('overdue 2 days')).toBeInTheDocument())
  })

  it('optimistic check-off: row disappears and api.post is called', async () => {
    mockBothOk([TASK_TODAY])
    renderWidget()
    await waitFor(() => screen.getByText('Fix bug'))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fix bug' }))
    await waitFor(() => expect(screen.queryByText('Fix bug')).toBeNull())
    expect(mockedPost).toHaveBeenCalledWith('/atlassian/confluence/task/t1/complete')
  })

  it('reverts row and shows inline message on check-off failure', async () => {
    mockedPost.mockRejectedValue(new Error('network error'))
    mockBothOk([TASK_TODAY])
    renderWidget()
    await waitFor(() => screen.getByText('Fix bug'))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fix bug' }))
    await waitFor(() => {
      expect(screen.getByText('Fix bug')).toBeInTheDocument()
      expect(screen.getByText("Couldn't check that off. It may have changed in Confluence.")).toBeInTheDocument()
    })
  })

  it('task row click navigates to /confluence/:page_id', async () => {
    mockBothOk([TASK_TODAY])
    const { container } = renderWidget()
    await waitFor(() => screen.getByText('Fix bug'))
    fireEvent.click(container.querySelector('[data-testid="task-row-t1"]') as HTMLElement)
    expect(mockNavigate).toHaveBeenCalledWith('/confluence/p1')
  })

  it('checkbox click does not trigger row navigation', async () => {
    mockBothOk([TASK_TODAY])
    renderWidget()
    await waitFor(() => screen.getByText('Fix bug'))
    mockNavigate.mockClear()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fix bug' }))
    await waitFor(() => expect(screen.queryByText('Fix bug')).toBeNull())
    expect(mockNavigate).not.toHaveBeenCalledWith('/confluence/p1')
  })

  it('shows empty state for action items', async () => {
    mockBothOk([])
    renderWidget()
    await waitFor(() => expect(screen.getByText('No action items assigned to you.')).toBeInTheDocument())
  })

  it('renders mention titles', async () => {
    mockBothOk([], [MENTION_ROW])
    renderWidget()
    await waitFor(() => expect(screen.getByText('Team sync notes')).toBeInTheDocument())
  })

  it('mention row click navigates to /confluence/:id', async () => {
    mockBothOk([], [MENTION_ROW])
    const { container } = renderWidget()
    await waitFor(() => screen.getByText('Team sync notes'))
    fireEvent.click(container.querySelector('[data-testid="mention-row-m1"]') as HTMLElement)
    expect(mockNavigate).toHaveBeenCalledWith('/confluence/m1')
  })

  it('shows empty state for mentions', async () => {
    mockBothOk([], [])
    renderWidget()
    await waitFor(() => expect(screen.getByText('No recent mentions.')).toBeInTheDocument())
  })

  it('tasks ok + mentions error: tasks shown, mentions section shows error', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/confluence/my-tasks')) return Promise.resolve({ tasks: [TASK_TODAY] })
      if (path.includes('/atlassian/confluence/query')) return Promise.reject(new Error('fail'))
      return Promise.reject(new Error('unexpected'))
    })
    renderWidget()
    await waitFor(() => {
      expect(screen.getByText('Fix bug')).toBeInTheDocument()
      expect(screen.getByText('Not connected or failed to load')).toBeInTheDocument()
    })
  })

  it('tasks error + mentions ok: mentions shown, tasks section shows error', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/confluence/my-tasks')) return Promise.reject(new Error('fail'))
      if (path.includes('/atlassian/confluence/query')) return Promise.resolve({ rows: [MENTION_ROW] })
      return Promise.reject(new Error('unexpected'))
    })
    renderWidget()
    await waitFor(() => {
      expect(screen.getByText('Team sync notes')).toBeInTheDocument()
      expect(screen.getByText('Not connected or failed to load')).toBeInTheDocument()
    })
  })

  it('both sections show independent errors when both fail', async () => {
    mockedGet.mockRejectedValue(new Error('Not connected'))
    renderWidget()
    await waitFor(() => {
      const msgs = screen.getAllByText('Not connected or failed to load')
      expect(msgs).toHaveLength(2)
    })
  })

  it('header card click navigates to /confluence', async () => {
    mockBothOk([], [])
    const { container } = renderWidget()
    await waitFor(() => expect(screen.getByText('Confluence')).toBeInTheDocument())
    fireEvent.click(container.querySelector('[data-testid="widget-confluence"] > *') as HTMLElement)
    expect(mockNavigate).toHaveBeenCalledWith('/confluence')
  })
})
