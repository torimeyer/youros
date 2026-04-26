import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Tasks from './Tasks'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
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
const mockedApiPost = vi.mocked(api.post)

const mockTasks = [
  { id: '1', title: 'Fix login bug', priority: 'P0', status: 'open', created_at: new Date().toISOString(), goal: 'Auth', label_ids: ['l1'] },
  { id: '2', title: 'Add dark mode', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: 'UI', label_ids: [] },
  { id: '3', title: 'Write docs', priority: 'P2', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: ['l1', 'l2'] },
  { id: '4', title: 'Old completed task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [] },
]

const mockLabels = [
  { id: 'l1', name: 'Bug', color: '#ef4444', task_count: 2 },
  { id: 'l2', name: 'Docs', color: '#3b82f6', task_count: 1 },
]

function renderTasks() {
  return render(
    <MemoryRouter>
      <Tasks />
    </MemoryRouter>
  )
}

/**
 * Single-select status filter. Clicking a pill selects it exclusively.
 */
function selectOnlyStatus(status: 'all' | 'open' | 'in_progress' | 'closed') {
  fireEvent.click(screen.getByTestId(`status-filter-${status}`))
}

describe('Tasks page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint tasks cache between tests so state does
    // not leak from one test to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('renders task list from API data', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Write docs')).toBeInTheDocument()
  })

  it('calls api.get with /tasks on mount', async () => {
    renderTasks()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks')
    })
  })

  it('also fetches labels on mount', async () => {
    renderTasks()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/labels')
    })
  })

  it('shows loading state before tasks arrive', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderTasks()

    // LoadingState skeleton-list renders a data-testid="loading-state" element
    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
  })

  it('skeleton list renders when both data and localStorage are empty', () => {
    window.localStorage.clear()
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderTasks()

    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
  })

  it('PageHeader renders the page title', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('page-header')).toBeInTheDocument()
    })
    expect(screen.getByTestId('page-header')).toHaveTextContent('Tasks')
  })

  it('renders the export button in the overflow menu', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Export button lives inside overflow menu
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    expect(screen.getByTestId('export-button')).toBeInTheDocument()
  })

  it('filter buttons show correct counts', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open filter drawer to access status filter buttons

    const openButton = screen.getByTestId('status-filter-open')
    expect(openButton).toHaveTextContent('3')

    const closedButton = screen.getByTestId('status-filter-closed')
    expect(closedButton).toHaveTextContent('1')
  })

  it('defaults to All filter (shows all tasks including closed)', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // "All" pill must be active by default
    expect(screen.getByTestId('status-filter-all').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Write docs')).toBeInTheDocument()
    // Closed tasks are visible under "All"
    expect(screen.getByText('Old completed task')).toBeInTheDocument()
  })

  it('Open filter hides closed and in-progress tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    selectOnlyStatus('open')

    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Write docs')).toBeInTheDocument()
    expect(screen.queryByText('Old completed task')).not.toBeInTheDocument()
  })

  it('single-select: clicking a second pill replaces the first selection', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    selectOnlyStatus('open')
    expect(screen.getByTestId('status-filter-open').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('status-filter-all').getAttribute('aria-pressed')).toBe('false')

    selectOnlyStatus('closed')
    expect(screen.getByTestId('status-filter-closed').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('status-filter-open').getAttribute('aria-pressed')).toBe('false')
  })

  it('clicking Closed filter shows only closed tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    selectOnlyStatus('closed')

    expect(screen.getByText('Old completed task')).toBeInTheDocument()
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
  })

  it('under All, in-progress (runtime) tasks sort before open tasks', async () => {
    const older = '2024-01-01T00:00:00Z'
    const newer = '2026-01-01T00:00:00Z'
    const sortTasks = [
      { id: 'running1', title: 'Running task (older)', priority: 'P1', status: 'open', created_at: older, goal: null, label_ids: [] },
      { id: 'open1', title: 'Open task (newer)', priority: 'P1', status: 'open', created_at: newer, goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: sortTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/agents') return Promise.resolve({ agents: [{ status: 'running', task_id: 'running1' }] })
      return Promise.resolve({})
    })

    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Running task (older)')).toBeInTheDocument()
    })
    expect(screen.getByTestId('status-filter-all').getAttribute('aria-pressed')).toBe('true')

    const body = document.body.textContent || ''
    expect(body.indexOf('Running task (older)')).toBeLessThan(body.indexOf('Open task (newer)'))
  })

  it('under All, tasks with the same status sort newest first', async () => {
    const sortTasks = [
      { id: 't1', title: 'Older open', priority: 'P1', status: 'open', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [] },
      { id: 't2', title: 'Newer open', priority: 'P1', status: 'open', created_at: '2026-04-01T00:00:00Z', goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: sortTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Newer open')).toBeInTheDocument()
    })

    const body = document.body.textContent || ''
    expect(body.indexOf('Newer open')).toBeLessThan(body.indexOf('Older open'))
  })

  it('closed filter shows tasks sorted by closed_at descending', async () => {
    const closedTasks = [
      { id: 'c1', title: 'Closed oldest', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-01-10T00:00:00Z', goal: null, label_ids: [] },
      { id: 'c2', title: 'Closed newest', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-04-20T12:00:00Z', goal: null, label_ids: [] },
      { id: 'c3', title: 'Closed middle', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-03-15T06:00:00Z', goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: closedTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('status-filter-closed')).toBeInTheDocument()
    })
    selectOnlyStatus('closed')

    await waitFor(() => {
      expect(screen.getByText('Closed newest')).toBeInTheDocument()
    })

    const bodyText = document.body.textContent || ''
    const positions = {
      newest: bodyText.indexOf('Closed newest'),
      middle: bodyText.indexOf('Closed middle'),
      oldest: bodyText.indexOf('Closed oldest'),
    }
    expect(positions.newest).toBeGreaterThan(-1)
    expect(positions.middle).toBeGreaterThan(-1)
    expect(positions.oldest).toBeGreaterThan(-1)
    expect(positions.newest).toBeLessThan(positions.middle)
    expect(positions.middle).toBeLessThan(positions.oldest)
  })

  it('closed-only view sorts by closed_at descending (newest closed first)', async () => {
    // Regression: the old closed-sort toggle buttons were removed along with
    // the duplicate SORT row; the closed-only view still defaults to newest
    // closed_at first without any extra toolbar.
    const closedTasks = [
      { id: 'c1', title: 'Closed oldest', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-01-10T00:00:00Z', goal: null, label_ids: [] },
      { id: 'c2', title: 'Closed newest', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-04-20T12:00:00Z', goal: null, label_ids: [] },
      { id: 'c3', title: 'Closed middle', priority: 'P1', status: 'closed', created_at: '2026-01-01T00:00:00Z', closed_at: '2026-03-15T06:00:00Z', goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: closedTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()
    await waitFor(() => {
      expect(screen.getByTestId('status-filter-closed')).toBeInTheDocument()
    })
    selectOnlyStatus('closed')

    await waitFor(() => {
      expect(screen.getByText('Closed newest')).toBeInTheDocument()
    })

    const body = document.body.textContent || ''
    expect(body.indexOf('Closed newest')).toBeLessThan(body.indexOf('Closed middle'))
    expect(body.indexOf('Closed middle')).toBeLessThan(body.indexOf('Closed oldest'))

    // Regression: removed closed-sort toggle buttons stay gone.
    expect(screen.queryByTestId('closed-sort-newest')).not.toBeInTheDocument()
    expect(screen.queryByTestId('closed-sort-oldest')).not.toBeInTheDocument()
  })

  it('sort-by buttons are rendered in the filter drawer', async () => {
    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })
    expect(screen.getByTestId('sort-by-date-desc')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-date-asc')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-status')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-label')).toBeInTheDocument()
  })

  it('sort by date ascending shows oldest task first', async () => {
    const dateTasks = [
      { id: 't1', title: 'Newest task', priority: 'P1', status: 'open', created_at: '2026-04-20T12:00:00Z', goal: null, label_ids: [] },
      { id: 't2', title: 'Oldest task', priority: 'P1', status: 'open', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [] },
      { id: 't3', title: 'Middle task', priority: 'P1', status: 'open', created_at: '2025-06-15T06:00:00Z', goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: dateTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Newest task')).toBeInTheDocument()
    })

    // All view: fixed sort (status → priority → date), newest first by default
    const defaultBody = document.body.textContent || ''
    expect(defaultBody.indexOf('Newest task')).toBeLessThan(defaultBody.indexOf('Oldest task'))

    // Open filter respects date-asc sortBy
    selectOnlyStatus('open')
    fireEvent.click(screen.getByTestId('sort-by-date-asc'))
    await waitFor(() => {
      const ascBody = document.body.textContent || ''
      expect(ascBody.indexOf('Oldest task')).toBeLessThan(ascBody.indexOf('Newest task'))
    })
  })

  it('sort by label groups tasks alphabetically by label name', async () => {
    const labelTasks = [
      { id: 'tA', title: 'Zebra task', priority: 'P1', status: 'open', created_at: '2026-04-01T00:00:00Z', goal: null, label_ids: ['l2'] },
      { id: 'tB', title: 'Alpha task', priority: 'P1', status: 'open', created_at: '2026-04-01T00:00:00Z', goal: null, label_ids: ['l1'] },
      { id: 'tC', title: 'No label task', priority: 'P1', status: 'open', created_at: '2026-04-01T00:00:00Z', goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: labelTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Zebra task')).toBeInTheDocument()
    })

    // Open filter respects label sortBy
    selectOnlyStatus('open')
    fireEvent.click(screen.getByTestId('sort-by-label'))
    await waitFor(() => {
      const body = document.body.textContent || ''
      // Bug (l1) < Docs (l2), so Alpha task should appear before Zebra task
      expect(body.indexOf('Alpha task')).toBeLessThan(body.indexOf('Zebra task'))
      // Tasks without labels sort last
      expect(body.indexOf('Zebra task')).toBeLessThan(body.indexOf('No label task'))
    })
  })

  it('clicking Open filter after Closed shows open tasks again', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    selectOnlyStatus('closed')
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()

    selectOnlyStatus('open')
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.queryByText('Old completed task')).not.toBeInTheDocument()
  })

  it('quick-add input and button calls POST /api/tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('What needs to be done?')
    fireEvent.change(input, { target: { value: 'New test task' } })

    const addButtons = screen.getAllByText('add')
    const addButton = addButtons[addButtons.length - 1].closest('button')!
    fireEvent.click(addButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks', { title: 'New test task', priority: 'P1' })
    })
  })

  it('quick-add clears input after successful submission', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('What needs to be done?') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'New test task' } })
    expect(input.value).toBe('New test task')

    const addButton = (() => { const btns = screen.getAllByText('add'); return btns[btns.length - 1].closest('button')!; })()
    fireEvent.click(addButton)

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('quick-add does not call API with empty input', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const addButton = (() => { const btns = screen.getAllByText('add'); return btns[btns.length - 1].closest('button')!; })()
    fireEvent.click(addButton)

    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('quick-add works with Enter key', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('What needs to be done?')
    fireEvent.change(input, { target: { value: 'Enter key task' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks', { title: 'Enter key task', priority: 'P1' })
    })
  })

  it('close button calls POST /api/tasks/{id}/close', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const closeButtons = screen.getAllByTitle('Close task')
    fireEvent.click(closeButtons[0])

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/1/close')
    })
  })

  it('reopen button calls POST /api/tasks/{id}/reopen', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('status-filter-closed'))

    await waitFor(() => {
      expect(screen.getByText('Old completed task')).toBeInTheDocument()
    })

    const reopenButton = screen.getByTitle('Reopen task')
    fireEvent.click(reopenButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/4/reopen')
    })
  })

  it('displays label pills on tasks that have labels', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Task 1 has label l1 ("Bug"), Task 3 has l1 and l2 ("Bug" and "Docs")
    // "Bug" label should appear at least twice (on tasks 1 and 3)
    const bugLabels = screen.getAllByText('Bug')
    expect(bugLabels.length).toBeGreaterThanOrEqual(2)

    // "Docs" label should appear on task 3
    const docsLabels = screen.getAllByText('Docs')
    expect(docsLabels.length).toBeGreaterThanOrEqual(1)
  })

  it('shows "No tasks match this filter" when filter yields no results', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '1', title: 'Only open', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Only open')).toBeInTheDocument()
    })

    selectOnlyStatus('closed')
    expect(screen.getByText('No tasks match this filter.')).toBeInTheDocument()
  })

  it('displays task IDs with # prefix', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument()
    })

    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('#3')).toBeInTheDocument()
  })

  it('displays priority badges on tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const priorities = screen.getAllByText(/^P[012]$/)
    expect(priorities.length).toBeGreaterThanOrEqual(3)
  })

  it('shows footer with open and closed counts', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const openTexts = screen.getAllByText('Open')
    const closedTexts = screen.getAllByText('Closed')
    expect(openTexts.length).toBeGreaterThanOrEqual(1)
    expect(closedTexts.length).toBeGreaterThanOrEqual(1)
  })

  it('refetches tasks after closing a task', async () => {
    renderTasks()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks')
    })

    const closeButtons = screen.getAllByTitle('Close task')
    fireEvent.click(closeButtons[0])

    await waitFor(() => {
      // Should have called /tasks at least twice (initial + refetch)
      const tasksCalls = mockedApiGet.mock.calls.filter((c) => c[0] === '/tasks')
      expect(tasksCalls.length).toBeGreaterThanOrEqual(2)
    })
  })

  // --- Stale Indicator ---

  it('shows stale indicator on open tasks older than 7 days', async () => {
    const oldDate = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '10', title: 'Old open task', priority: 'P1', status: 'open', created_at: oldDate, goal: null, label_ids: [] },
          { id: '11', title: 'Fresh task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Old open task')).toBeInTheDocument()
    })

    const staleLabels = screen.getAllByText('stale')
    expect(staleLabels).toHaveLength(1)
  })

  it('does not show stale indicator on closed tasks even if old', async () => {
    const oldDate = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '10', title: 'Old closed task', priority: 'P1', status: 'closed', created_at: oldDate, goal: null, label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.queryByText('Old closed task')).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('status-filter-closed'))

    await waitFor(() => {
      expect(screen.getByText('Old closed task')).toBeInTheDocument()
    })

    expect(screen.queryByText('stale')).not.toBeInTheDocument()
  })

  it('does not show stale indicator on tasks created less than 7 days ago', async () => {
    const recentDate = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '10', title: 'Recent task', priority: 'P1', status: 'open', created_at: recentDate, goal: null, label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Recent task')).toBeInTheDocument()
    })

    expect(screen.queryByText('stale')).not.toBeInTheDocument()
  })

  // --- Labels tab ---

  it('shows Labels tab that switches to LabelsView', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const labelsTab = screen.getByRole('button', { name: 'Labels' })
    expect(labelsTab).toBeInTheDocument()
  })

  // --- Task context briefing panel ---

  it('clicking a task shows the briefing panel', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: 'point=1, 2 members', neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
    })
  })

  it('briefing panel fetches from /tasks/{id}/briefing on click', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/1/briefing')
    })
  })

  it('briefing panel shows blockers when present', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [],
          blocked_by: [
            { text: '#5 Setup auth provider', resolved: false },
            { text: '#3 Write docs', resolved: true },
          ],
          unblocks: [],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByText('Waiting on')).toBeInTheDocument()
      expect(screen.getByText('#5 Setup auth provider')).toBeInTheDocument()
      expect(screen.getByText('#3 Write docs')).toBeInTheDocument()
    })
  })

  it('briefing panel shows enriched blocker card with title, priority, and status', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [],
          blocked_by: [
            {
              text: '\u2192160 [open] Mobile-friendly layout',
              resolved: false,
              blocker_id: '160',
              blocker_task: {
                id: '\u2192160',
                title: 'Mobile-friendly layout',
                description: 'Make every page work on a phone screen',
                priority: 'P1',
                status: 'open',
              },
              explanation: 'The dashboard needs to work on phones first.',
            },
          ],
          unblocks: [],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByText('Waiting on')).toBeInTheDocument()
    })
    const card = screen.getByTestId('blocker-card-0')
    // Card content: id reference, title, priority, status, description, explanation.
    expect(card).toHaveTextContent('Blocked by \u2192160')
    expect(card).toHaveTextContent('Mobile-friendly layout')
    expect(card).toHaveTextContent('P1')
    expect(card).toHaveTextContent('Open')
    expect(card).toHaveTextContent('Make every page work on a phone screen')
    expect(card).toHaveTextContent('The dashboard needs to work on phones first.')
  })

  it('briefing panel shows multiple blocker cards', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [],
          blocked_by: [
            {
              text: '\u2192100 [open] Tests',
              resolved: false,
              blocker_id: '100',
              blocker_task: { id: '\u2192100', title: 'Tests', description: '', priority: 'P1', status: 'open' },
              explanation: null,
            },
            {
              text: '\u2192101 [open] Docs',
              resolved: false,
              blocker_id: '101',
              blocker_task: { id: '\u2192101', title: 'Docs', description: '', priority: 'P2', status: 'open' },
              explanation: null,
            },
          ],
          unblocks: [],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('blocker-card-0')).toBeInTheDocument()
      expect(screen.getByTestId('blocker-card-1')).toBeInTheDocument()
    })
    expect(screen.getByTestId('blocker-card-0')).toHaveTextContent('Tests')
    expect(screen.getByTestId('blocker-card-1')).toHaveTextContent('Docs')
  })

  it('briefing panel hides Waiting on section when no blockers', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [], blocked_by: [], unblocks: [],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
    })
    expect(screen.queryByText('Waiting on')).not.toBeInTheDocument()
    expect(screen.queryByTestId('blocker-card-0')).not.toBeInTheDocument()
  })

  it('briefing panel shows unblocks when present', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [],
          blocked_by: [],
          unblocks: ['#7 Deploy to production'],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByText('Finishing this unblocks')).toBeInTheDocument()
      expect(screen.getByText('#7 Deploy to production')).toBeInTheDocument()
    })
  })

  it('briefing panel shows standalone message when no context', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: {
          task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug',
          sphere: null, neighbors: [], blocked_by: [], unblocks: [],
          all_blockers_resolved: false, raw: ''
        }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByText('This task is standalone. No blockers, no dependencies, no related tasks.')).toBeInTheDocument()
    })
  })

  it('clicking the same task again closes the briefing panel', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open
    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
    })

    // Close by clicking again
    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.queryByTestId('briefing-panel')).not.toBeInTheDocument()
    })
  })

  it('shows Context and Changelog tabs in the briefing panel', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: 'Created from idea', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: ['abc123'] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
    })

    // Should see Context and Changelog tab buttons
    expect(screen.getByRole('button', { name: 'Context' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Changelog' })).toBeInTheDocument()
  })

  // --- Changelog / Trace panel ---

  it('fetches trace data when a task is clicked', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/1/trace')
    })
  })

  it('clicking Changelog tab shows the trace panel with commits', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: {
          headline: '#1: Fix login bug [P0, open]',
          specs: ['design/auth.md'],
          drafts: [],
          agentfiles: [],
          depends_on: [],
          blocks: ['#7 Deploy'],
          commits: ['abc1234 Fix auth redirect']
        }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('changelog-tab')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('changelog-tab'))

    await waitFor(() => {
      expect(screen.getByTestId('trace-panel')).toBeInTheDocument()
      expect(screen.getByText('Specs')).toBeInTheDocument()
      expect(screen.getByText('design/auth.md')).toBeInTheDocument()
      expect(screen.getByText('Blocks')).toBeInTheDocument()
      expect(screen.getByText('#7 Deploy')).toBeInTheDocument()
      expect(screen.getByText('Commits')).toBeInTheDocument()
      expect(screen.getByText('abc1234 Fix auth redirect')).toBeInTheDocument()
    })
  })

  it('Changelog tab shows empty message when trace has no data', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      if (path.includes('/briefing')) return Promise.resolve({
        briefing: { task_id: '1', priority: 'P0', status: 'open', title: 'Fix login bug', sphere: null, neighbors: [], blocked_by: [], unblocks: [], all_blockers_resolved: false, raw: '' }
      })
      if (path.includes('/trace')) return Promise.resolve({
        trace: { headline: '', specs: [], drafts: [], agentfiles: [], depends_on: [], blocks: [], commits: [] }
      })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix login bug'))

    await waitFor(() => {
      expect(screen.getByTestId('changelog-tab')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('changelog-tab'))

    await waitFor(() => {
      expect(screen.getByText('No history yet. Specs, drafts, commits, and connections will appear here as work happens.')).toBeInTheDocument()
    })
  })


  // --- Health tab ---

  it('shows Health tab button', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const healthTab = screen.getByRole('button', { name: 'Health' })
    expect(healthTab).toBeInTheDocument()
  })

  // --- Auto-applied labels ---

  describe('auto-applied labels', () => {
    const autoTasks = [
      {
        id: '1',
        title: 'Fix login bug',
        priority: 'P0',
        status: 'open',
        created_at: new Date().toISOString(),
        goal: null,
        label_ids: ['l1'],
        auto_label_ids: ['l1'],
      },
    ]

    beforeEach(() => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: autoTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        return Promise.resolve({})
      })
    })

    it('clicking an auto-applied label removes it via the API', async () => {
      const mockedDelete = vi.mocked(api.delete)
      mockedDelete.mockResolvedValue({ label_ids: [] })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })

      const labelEls = screen.getAllByText('Bug')
      // The task label pill is the first one in a span (not the filter drawer button)
      const pill = labelEls.map(el => el.closest('span[class*="rounded-full"]')).find(el => el !== null) as HTMLElement
      expect(pill).not.toBeNull()
      fireEvent.click(pill)

      await waitFor(() => {
        expect(mockedDelete).toHaveBeenCalledWith('/tasks/1/labels/l1')
      })
    })
  })

  // --- Deep-link from Dashboard via ?focus=<id> ---

  describe('focus query param deep-link', () => {
    const focusTasks = [
      { id: '\u2192123', title: 'Deep linked task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: '\u2192124', title: 'Another task', priority: 'P2', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: '\u2192125', title: 'A closed task', priority: 'P1', status: 'closed', created_at: new Date().toISOString(), goal: null, label_ids: [] },
    ]

    beforeEach(() => {
      // Make scrollIntoView a no-op so jsdom does not throw.
      Element.prototype.scrollIntoView = vi.fn()

      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: focusTasks })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path.endsWith('/briefing')) return Promise.resolve({ briefing: null })
        if (path.endsWith('/trace')) return Promise.resolve({ trace: null })
        return Promise.resolve({})
      })
    })

    function renderWithFocus(focusId: string) {
      // Encode the id the same way Dashboard encodes it.
      const url = `/tasks?focus=${encodeURIComponent(focusId)}`
      return render(
        <MemoryRouter initialEntries={[url]}>
          <Tasks />
        </MemoryRouter>
      )
    }

    it('auto-expands the briefing panel for the task in ?focus=', async () => {
      renderWithFocus('\u2192123')

      await waitFor(() => {
        expect(screen.getByText('Deep linked task')).toBeInTheDocument()
      })

      // The briefing panel should appear for the focused task.
      await waitFor(() => {
        expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
      })

      // The briefing endpoint for the focused task should be called.
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/\u2192123/briefing')
    })

    it('switches to the Closed filter when the focused task is closed', async () => {
      renderWithFocus('\u2192125')

      await waitFor(() => {
        expect(screen.getByText('A closed task')).toBeInTheDocument()
      })

      // Briefing panel should be open for the closed task.
      await waitFor(() => {
        expect(screen.getByTestId('briefing-panel')).toBeInTheDocument()
      })

      // The open tasks should no longer be visible because we swapped to Closed.
      expect(screen.queryByText('Deep linked task')).not.toBeInTheDocument()
    })

    it('does nothing when ?focus=<unknown-id> is given', async () => {
      renderWithFocus('\u2192999')

      await waitFor(() => {
        expect(screen.getByText('Deep linked task')).toBeInTheDocument()
      })

      // No briefing panel should appear because there was no match.
      expect(screen.queryByTestId('briefing-panel')).not.toBeInTheDocument()
    })
  })

  describe('in_progress tasks are treated as active (needle 277)', () => {
    // Regression for the "where did all my tasks go?" bug. The ostk
    // state machine produces three terminal-ish statuses: "open",
    // "in_progress", and "closed". Before the fix, the Open tab and
    // every count badge filtered strictly for status === "open", so
    // every in_progress task disappeared from the UI. With 13
    // in_progress tasks and 1 open task on Tori's workspace, the
    // page showed 1 task instead of 14. Lock that in.
    const mixedStatusTasks = [
      { id: 'a1', title: 'Active open task', priority: 'P0', status: 'open',        created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: 'a2', title: 'Active in-progress one', priority: 'P1', status: 'in_progress', created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: 'a3', title: 'Active in-progress two', priority: 'P2', status: 'in_progress', created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: 'a4', title: 'Done dusted and gone',  priority: 'P1', status: 'closed',     created_at: new Date().toISOString(), goal: null, label_ids: [] },
    ]

    beforeEach(() => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mixedStatusTasks })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })
    })

    it('Open tab shows in_progress tasks alongside open tasks', async () => {
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })
      // Select "Open" filter — tasks with stored status in_progress but no
      // running agent have effective status "open" and must appear here.
      selectOnlyStatus('open')
      expect(screen.getByText('Active open task')).toBeInTheDocument()
      expect(screen.getByText('Active in-progress one')).toBeInTheDocument()
      expect(screen.getByText('Active in-progress two')).toBeInTheDocument()
      expect(screen.queryByText('Done dusted and gone')).not.toBeInTheDocument()
    })

    it('Open and In Progress pills split the active task counts correctly', async () => {
      // After the runtime-only fix, the Open pill counts all active tasks not
      // currently being worked on by a live agent. The In Progress pill counts
      // only tasks with a running agent (runtime-derived). Stored
      // status=in_progress with no agent is treated as Open.
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })
      const openButton = screen.getByTestId('status-filter-open')
      expect(openButton).toHaveTextContent('3')
      const inProgressButton = screen.getByTestId('status-filter-in_progress')
      expect(inProgressButton).toHaveTextContent('0')
    })

    it('Closed tab still only shows closed tasks', async () => {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })

      selectOnlyStatus('closed')

      await waitFor(() => {
        expect(screen.getByText('Done dusted and gone')).toBeInTheDocument()
      })
      expect(screen.queryByText('Active open task')).not.toBeInTheDocument()
      expect(screen.queryByText('Active in-progress one')).not.toBeInTheDocument()
    })
  })

  // ── Needle 295: Comprehensive build vs Quick build ──────────────
  //
  // The Tasks page action menu now has a two-option build submenu.
  // "Comprehensive build" runs the full plan, build, test, verify
  // pattern. A small help icon next to it opens a plain-language
  // popover explaining what comprehensive build does. "Quick build"
  // is the legacy fast draft with no gates. The bulk Implement all
  // button uses comprehensive by default. The backend accepts both
  // "comprehensive" and the "saa" alias, but the UI posts the
  // canonical name.
  describe('Comprehensive build vs Quick build (needle 295)', () => {
    async function openFirstActionMenu() {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      const actionButtons = screen.getAllByTitle('Actions')
      fireEvent.click(actionButtons[0])
      await waitFor(() => {
        expect(screen.getByText('Comprehensive build')).toBeInTheDocument()
      })
    }

    it('action menu shows both build options', async () => {
      await openFirstActionMenu()
      expect(screen.getByText('Comprehensive build')).toBeInTheDocument()
      expect(screen.getByText('Quick build')).toBeInTheDocument()
    })

    it('clicking Comprehensive build posts template=comprehensive to /agents/spawn', async () => {
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Comprehensive build'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/agents/spawn',
          expect.objectContaining({ template: 'comprehensive' })
        )
      })

      // The prompt should mention the comprehensive build pattern so
      // the agent sees the plan, build, test, verify framing even
      // before the template envelope is prepended server side.
      const spawnCall = mockedApiPost.mock.calls.find(
        (c) => c[0] === '/agents/spawn'
      )
      expect(spawnCall).toBeTruthy()
      const body = spawnCall![1] as { template?: string; prompt: string; name: string; task_id: string }
      expect(body.template).toBe('comprehensive')
      expect(body.name.startsWith('implement-')).toBe(true)
      // The originating task id must be included so the backend can
      // mark the task as in_progress while the agent is working, no
      // matter which spawn path created it.
      expect(body.task_id).toBe('1')
    })

    it('clicking Quick build does not send a template field', async () => {
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Quick build'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/agents/spawn',
          expect.objectContaining({ name: expect.stringContaining('implement-') })
        )
      })

      const spawnCall = mockedApiPost.mock.calls.find(
        (c) => c[0] === '/agents/spawn'
      )
      const body = spawnCall![1] as { template?: string }
      expect(body.template).toBeUndefined()
    })

    // --- Spawn locks contract (matches api/services/spawn_isolation.py) ---
    // Plan mode is read-only (no edit verbs) → isolation="none" → locks:["*"] is the opt-out.
    // Comprehensive and Quick modes contain "Implement" → isolation="worktree" → must declare
    // real paths; locks:["*"] is rejected by validate_locks_for_spawn for worktree spawns.
    it('plan mode sends locks:["*"] and isolation:"none" (read-only opt-out)', async () => {
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Plan'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/agents/spawn',
          expect.objectContaining({ locks: ['*'] })
        )
      })

      const spawnCall = mockedApiPost.mock.calls.find((c) => c[0] === '/agents/spawn')
      const body = spawnCall![1] as { locks: string[]; isolation?: string }
      expect(body.locks).toEqual(['*'])
      // isolation:"none" is required so the server doesn't route "Create …" verbs
      // through decide_isolation → "worktree", which rejects locks:["*"].
      expect(body.isolation).toBe('none')
    })

    it('comprehensive mode sends real path globs, not the wildcard opt-out', async () => {
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Comprehensive build'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/agents/spawn',
          expect.objectContaining({ template: 'comprehensive' })
        )
      })

      const spawnCall = mockedApiPost.mock.calls.find((c) => c[0] === '/agents/spawn')
      const body = spawnCall![1] as { locks: string[] }
      // Must not be the wildcard — server rejects it for edit-capable spawns.
      expect(body.locks).not.toContain('*')
      // Must be non-empty so the server has real paths to lock.
      expect(body.locks.length).toBeGreaterThan(0)
      // Must cover the main code directories.
      expect(body.locks.some((g) => g.startsWith('app/'))).toBe(true)
      expect(body.locks.some((g) => g.startsWith('api/'))).toBe(true)
    })

    it('quick build mode sends real path globs, not the wildcard opt-out', async () => {
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Quick build'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/agents/spawn',
          expect.objectContaining({ name: expect.stringContaining('implement-') })
        )
      })

      const spawnCall = mockedApiPost.mock.calls.find((c) => c[0] === '/agents/spawn')
      const body = spawnCall![1] as { locks: string[] }
      expect(body.locks).not.toContain('*')
      expect(body.locks.length).toBeGreaterThan(0)
      expect(body.locks.some((g) => g.startsWith('app/'))).toBe(true)
      expect(body.locks.some((g) => g.startsWith('api/'))).toBe(true)
    })

    it('help icon opens the plain-language popover and Escape closes it', async () => {
      await openFirstActionMenu()

      // Click the "What does this do?" info icon next to
      // the Comprehensive build option.
      const helpButtons = screen.getAllByLabelText('What does this do?')
      fireEvent.click(helpButtons[0])

      // The popover dialog appears with the plain-language copy.
      const dialog = await screen.findByRole('dialog', { name: 'What does this do?' })
      expect(dialog).toBeInTheDocument()
      expect(dialog).toHaveTextContent('Reads the task and plans the approach')
      expect(dialog).toHaveTextContent('Runs pytest and tsc to catch regressions')
      expect(dialog).toHaveTextContent('Only reports done when everything is green')

      // Escape closes it.
      fireEvent.keyDown(document, { key: 'Escape' })
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'What does this do?' })).not.toBeInTheDocument()
      })
    })

    it('clicking outside the help popover closes it', async () => {
      await openFirstActionMenu()

      const helpButtons = screen.getAllByLabelText('What does this do?')
      fireEvent.click(helpButtons[0])

      await screen.findByRole('dialog', { name: 'What does this do?' })

      // Mousedown on document body (outside the popover) closes it.
      fireEvent.mouseDown(document.body)

      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'What does this do?' })).not.toBeInTheDocument()
      })
    })

    it('shows banner text after Comprehensive build is triggered', async () => {
      // Regression: the banner appeared as an empty purple bar because
      // text-purple-200 was invisible in light mode (no CSS override).
      // The banner must contain visible text after a successful spawn.
      mockedApiPost.mockResolvedValue({ result: "Agent 'implement-1' spawned", pid: 123 })
      await openFirstActionMenu()

      fireEvent.click(screen.getByText('Comprehensive build'))

      await waitFor(() => {
        const banner = screen.getByText(/comprehensive build started/i)
        expect(banner).toBeInTheDocument()
        expect(banner.textContent!.trim().length).toBeGreaterThan(0)
      })
    })

    it('does not render banner when value is empty or whitespace', async () => {
      // Regression guard: even if setBanner is called with whitespace,
      // the banner div must not appear. The guard `banner.trim()` in the
      // JSX conditional prevents an empty purple bar.
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      // There should be no banner div visible by default
      const bannerElements = document.querySelectorAll('.bg-purple-500\\/20')
      expect(bannerElements.length).toBe(0)
    })

    it('bulk Implement all posts template=comprehensive for every selected task', async () => {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })

      // Select two open tasks by clicking the row checkboxes.
      const checkboxes = screen
        .getAllByRole('checkbox')
        .filter((el) => (el as HTMLInputElement).type === 'checkbox')
      fireEvent.click(checkboxes[0])
      fireEvent.click(checkboxes[1])

      // The bulk toolbar's "Implement all" button appears.
      const implementAll = await screen.findByRole('button', {
        name: /implement all/i,
      })
      fireEvent.click(implementAll)

      await waitFor(() => {
        const spawnCalls = mockedApiPost.mock.calls.filter(
          (c) => c[0] === '/agents/spawn'
        )
        expect(spawnCalls.length).toBeGreaterThanOrEqual(1)
        for (const call of spawnCalls) {
          const body = call[1] as { template?: string }
          expect(body.template).toBe('comprehensive')
        }
      })
    })
  })

  // Suggestions UI removed (→249). Tests will be restored when the feature is polished.

  describe('Tasks audit feature', () => {
    const nowIso = new Date().toISOString()
    const auditClosedTasks = [
      { id: '1', title: 'Fix login bug', priority: 'P0', status: 'open', created_at: nowIso, goal: null, label_ids: [] },
      { id: '2', title: 'Done task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [], closed_reason: 'completed' },
      { id: '3', title: 'Duplicate task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [], closed_reason: 'duplicate' },
      { id: '4', title: 'Archived task', priority: 'P2', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [], closed_reason: 'archived' },
    ]

    it('renders the Audit for review button in the overflow menu', async () => {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      // Open overflow menu to reveal audit button
      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      const button = screen.getByTestId('tasks-audit-button')
      expect(button).toBeInTheDocument()
      expect(button.textContent).toContain('Audit for review')
    })

    it('clicking Audit opens the modal and posts /tasks/audit', async () => {
      mockedApiPost.mockImplementation((path: string) => {
        if (path === '/tasks/audit') return Promise.resolve({ job_id: 'job-abc' })
        return Promise.resolve({})
      })
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        if (path === '/tasks/audit/job-abc') {
          return Promise.resolve({
            status: 'running',
            checked: 0,
            total: 3,
            results: { closed: [], review: [], skipped_irl: 0, errors: [] },
          })
        }
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      fireEvent.click(screen.getByTestId('tasks-audit-button'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith('/tasks/audit')
      })
      expect(screen.getByTestId('tasks-audit-modal')).toBeInTheDocument()
    })

    it('shows progress line while audit job is running', async () => {
      mockedApiPost.mockImplementation((path: string) => {
        if (path === '/tasks/audit') return Promise.resolve({ job_id: 'job-running' })
        return Promise.resolve({})
      })
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        if (path === '/tasks/audit/job-running') {
          return Promise.resolve({
            status: 'running',
            checked: 2,
            total: 5,
            results: { closed: [], review: [], skipped_irl: 0, errors: [] },
          })
        }
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      fireEvent.click(screen.getByTestId('tasks-audit-button'))

      await waitFor(() => {
        const progress = screen.getByTestId('audit-progress')
        expect(progress.textContent).toContain('Checked 2 of 5')
      })
    })

    it('review list renders each row with Close and Keep buttons', async () => {
      mockedApiPost.mockImplementation((path: string) => {
        if (path === '/tasks/audit') return Promise.resolve({ job_id: 'job-review' })
        return Promise.resolve({})
      })
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        if (path === '/tasks/audit/job-review') {
          return Promise.resolve({
            status: 'done',
            checked: 3,
            total: 3,
            results: {
              closed: [],
              review: [
                {
                  task_id: 'r-1',
                  title: 'Old thing',
                  reason_guess: 'completed',
                  reason_label: 'This looks already done',
                  evidence: 'exists in api/x.py',
                  confidence: 'medium',
                },
                {
                  task_id: 'r-2',
                  title: 'Other thing',
                  reason_guess: 'duplicate',
                  reason_label: 'This looks like a duplicate of t-9',
                  evidence: 'same as t-9',
                  confidence: 'medium',
                  duplicate_of: 't-9',
                },
              ],
              skipped_irl: 1,
              errors: [],
            },
          })
        }
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      fireEvent.click(screen.getByTestId('tasks-audit-button'))

      await waitFor(() => {
        expect(screen.getByTestId('audit-review-row-r-1')).toBeInTheDocument()
      })
      // Review headline must make clear nothing closes without approval.
      expect(screen.getByTestId('audit-review-headline').textContent).toContain(
        'Review every task. Nothing closes until you say so.',
      )
      expect(screen.getByTestId('audit-review-row-r-2')).toBeInTheDocument()
      const closeButton = screen.getByTestId('audit-close-r-1')
      expect(closeButton).toBeInTheDocument()
      expect(closeButton.textContent).toContain('Close this task')
      expect(screen.getByTestId('audit-keep-r-1')).toBeInTheDocument()
      expect(screen.getByText('This looks already done')).toBeInTheDocument()
      expect(screen.getByText(/duplicate of t-9/)).toBeInTheDocument()
      // Summary line reports plain-language counts.
      expect(screen.getByTestId('audit-summary').textContent).toContain(
        '1 done',
      )
      expect(screen.getByTestId('audit-summary').textContent).toContain(
        'Skipped 1 real-life task',
      )
    })

    it('clicking Close calls the approve endpoint', async () => {
      mockedApiPost.mockImplementation((path: string) => {
        if (path === '/tasks/audit') return Promise.resolve({ job_id: 'job-approve' })
        if (path.endsWith('/approve')) return Promise.resolve({ closed: 'r-1', reason: 'completed' })
        return Promise.resolve({})
      })
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        if (path === '/tasks/audit/job-approve') {
          return Promise.resolve({
            status: 'done',
            checked: 1,
            total: 1,
            results: {
              closed: [],
              review: [
                {
                  task_id: 'r-1',
                  title: 'Old thing',
                  reason_guess: 'completed',
                  reason_label: 'This looks already done',
                  evidence: 'exists in api/x.py',
                  confidence: 'medium',
                },
              ],
              skipped_irl: 0,
              errors: [],
            },
          })
        }
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      fireEvent.click(screen.getByTestId('tasks-audit-button'))

      await waitFor(() => {
        expect(screen.getByTestId('audit-close-r-1')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('audit-close-r-1'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/tasks/audit/job-approve/approve',
          { task_id: 'r-1', reason: 'completed' },
        )
      })
    })

    it('clicking Keep calls the reject endpoint', async () => {
      mockedApiPost.mockImplementation((path: string) => {
        if (path === '/tasks/audit') return Promise.resolve({ job_id: 'job-keep' })
        if (path.endsWith('/reject')) return Promise.resolve({ kept: 'r-1' })
        return Promise.resolve({})
      })
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        if (path === '/tasks/audit/job-keep') {
          return Promise.resolve({
            status: 'done',
            checked: 1,
            total: 1,
            results: {
              closed: [],
              review: [
                {
                  task_id: 'r-1',
                  title: 'Old thing',
                  reason_guess: 'completed',
                  reason_label: 'This looks already done',
                  evidence: 'exists in api/x.py',
                  confidence: 'medium',
                },
              ],
              skipped_irl: 0,
              errors: [],
            },
          })
        }
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
      fireEvent.click(screen.getByTestId('tasks-audit-button'))
      await waitFor(() => {
        expect(screen.getByTestId('audit-keep-r-1')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('audit-keep-r-1'))

      await waitFor(() => {
        expect(mockedApiPost).toHaveBeenCalledWith(
          '/tasks/audit/job-keep/reject',
          { task_id: 'r-1' },
        )
      })
    })

    it('renders closed_reason badges on closed tasks', async () => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: auditClosedTasks })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        return Promise.resolve({})
      })
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      // Switch to the Closed filter so the closed rows render.
        fireEvent.click(screen.getByTestId('status-filter-closed'))
      await waitFor(() => {
        expect(screen.getByText('Done task')).toBeInTheDocument()
      })
      expect(screen.getByTestId('closed-badge-2').textContent).toBe('Done')
      expect(screen.getByTestId('closed-badge-3').textContent).toBe('Duplicate')
      expect(screen.getByTestId('closed-badge-4').textContent).toBe('Archived')
    })

    it('renders closed_at date next to badge when closed_at is present', async () => {
      const tasksWithClosedAt = [
        { id: '1', title: 'Fix login bug', priority: 'P0', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
        { id: '2', title: 'Done task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [], closed_reason: 'completed', closed_at: '2026-04-20T12:00:00Z' },
        { id: '3', title: 'No date task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [], closed_reason: 'completed' },
      ]
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: tasksWithClosedAt })
        if (path === '/labels') return Promise.resolve({ labels: mockLabels })
        return Promise.resolve({})
      })
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
        fireEvent.click(screen.getByTestId('status-filter-closed'))
      await waitFor(() => {
        expect(screen.getByText('Done task')).toBeInTheDocument()
      })
      // Task with closed_at shows the date element
      expect(screen.getByTestId('closed-at-2')).toBeInTheDocument()
      // Task without closed_at does not show the date element
      expect(screen.queryByTestId('closed-at-3')).not.toBeInTheDocument()
    })
  })
})

// Regression for needle 299: the Tasks page was showing "Loading tasks..."
// for several seconds on first visit, even though the /tasks endpoint
// itself returned in under 50ms. This suite pins the load-time contract
// so the class of bug cannot silently return.
//
// Invariant:
//   1. The first visible task row must appear within 300ms of render
//      when the api mock resolves fast.
//   2. Primary data renders immediately even if secondary data like
//      /labels or /threads is slow to arrive.
//   3. When localStorage has a cached task list from a prior visit,
//      the very first render paints rows from that cache without
//      waiting on any network call.
describe('Tasks page - first-paint budget (needle 299)', () => {
  const manyTasks = Array.from({ length: 100 }, (_, i) => ({
    id: String(i + 1),
    title: `Load test task ${i + 1}`,
    priority: ['P0', 'P1', 'P2', 'P3'][i % 4],
    status: i < 25 ? 'open' : 'closed',
    created_at: new Date().toISOString(),
    label_ids: [],
  }))

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  const FIRST_ROW_BUDGET_MS = 300

  it('first visible row arrives within 300ms on a warm backend', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: manyTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/threads') return Promise.resolve({ threads: [] })
      return Promise.resolve({})
    })

    const t0 = performance.now()
    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Load test task 1')).toBeInTheDocument()
    })
    const elapsed = performance.now() - t0
    expect(elapsed).toBeLessThan(FIRST_ROW_BUDGET_MS)
  })

  it('renders task rows immediately even while /labels hangs for 2 seconds', async () => {
    // This locks in the "render primary data first, hydrate secondary
    // later" invariant. If some future refactor starts awaiting labels
    // before painting the task list, this test goes red loudly.
    //
    // Deterministic variant (no wall-clock). We hand out explicit
    // resolver handles for /labels and /threads and never call them,
    // so those requests stay pending forever. The test asserts that
    // task rows are visible while the resolvers are still pending.
    // If a future refactor starts awaiting /labels or /threads before
    // painting rows, the waitFor below will time out instead of the
    // assertion flickering on slow CI hardware.
    let labelsResolved = false
    let threadsResolved = false
    const pendingLabels = new Promise<{ labels: unknown[] }>(() => {
      // Intentionally never resolved. The test verifies the page
      // renders task rows even when this promise is still pending.
    })
    const pendingThreads = new Promise<{ threads: unknown[] }>(() => {
      // Same: intentionally never resolved.
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: manyTasks })
      if (path === '/labels') return pendingLabels
      if (path === '/threads') return pendingThreads
      return Promise.resolve({})
    })

    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Load test task 1')).toBeInTheDocument()
    })

    // The task rows painted while /labels and /threads are still
    // pending. This is the core invariant needle 299 locks in: primary
    // task data renders first, secondary data hydrates later.
    expect(labelsResolved).toBe(false)
    expect(threadsResolved).toBe(false)
    expect(screen.getByText('Load test task 1')).toBeInTheDocument()
  })

  it('paints rows from the localStorage cache before any fetch resolves', async () => {
    // Seed the cache the way a prior successful visit would have.
    window.localStorage.setItem(
      'myos.tasksCache.v1',
      JSON.stringify([
        {
          id: 'cached-1',
          title: 'Cached row from last visit',
          priority: 'P1',
          status: 'open',
          created_at: new Date().toISOString(),
          label_ids: [],
        },
      ]),
    )

    // Hang every endpoint so the test can ONLY succeed via the cache
    // path. If the page waits for the network, this test times out.
    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    renderTasks()

    // The cached row is visible on the synchronous first paint, no
    // waitFor needed. This is the core needle 299 invariant.
    expect(screen.getByText('Cached row from last visit')).toBeInTheDocument()
    // And the "Loading tasks..." hint must NOT appear over the cached
    // row.
    expect(screen.queryByText('Loading tasks...')).not.toBeInTheDocument()
  })

  it('overwrites the cache with the next successful /tasks response', async () => {
    // Seed stale cache with one row.
    window.localStorage.setItem(
      'myos.tasksCache.v1',
      JSON.stringify([
        {
          id: 'stale-1',
          title: 'Old cached row',
          priority: 'P1',
          status: 'open',
          created_at: new Date().toISOString(),
          label_ids: [],
        },
      ]),
    )

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: manyTasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/threads') return Promise.resolve({ threads: [] })
      return Promise.resolve({})
    })

    renderTasks()
    // Stale cache row paints first.
    expect(screen.getByText('Old cached row')).toBeInTheDocument()
    // Fresh data replaces it.
    await waitFor(() => {
      expect(screen.getByText('Load test task 1')).toBeInTheDocument()
    })
    // Cache is now updated to the fresh data.
    const persisted = JSON.parse(
      window.localStorage.getItem('myos.tasksCache.v1') || '[]',
    )
    expect(Array.isArray(persisted)).toBe(true)
    expect(persisted.length).toBe(manyTasks.length)
    expect(persisted[0].title).toBe('Load test task 1')
  })

})

describe('Tasks page - simplified toolbar (2-layer layout)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: mockTasks })
      if (path === '/labels') return Promise.resolve({ labels: mockLabels })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('renders primary toolbar with title and LIVE badge', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    expect(screen.getByTestId('primary-toolbar')).toBeInTheDocument()
    expect(screen.getByTestId('live-badge')).toBeInTheDocument()
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })

  it('renders "What should I do next?" AI button in primary row', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    expect(screen.getByTestId('what-should-i-do-next')).toBeInTheDocument()
  })

  it('renders overflow menu trigger button', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    expect(screen.getByTestId('overflow-menu-trigger')).toBeInTheDocument()
  })

  it('overflow menu is hidden by default', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    expect(screen.queryByTestId('overflow-menu')).not.toBeInTheDocument()
  })

  it('clicking overflow trigger opens the menu', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    expect(screen.getByTestId('overflow-menu')).toBeInTheDocument()
  })

  it('overflow menu contains Label all, Import, Share, Copy list, Audit for review', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    expect(screen.getByTestId('label-all-btn')).toBeInTheDocument()
    expect(screen.getByTestId('overflow-import')).toBeInTheDocument()
    expect(screen.getByTestId('overflow-share')).toBeInTheDocument()
    expect(screen.getByTestId('overflow-copy')).toBeInTheDocument()
    expect(screen.getByTestId('tasks-audit-button')).toBeInTheDocument()
  })

  // --- Delete all ---

  it('overflow menu has a Delete all item', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    expect(screen.getByTestId('overflow-delete-all')).toBeInTheDocument()
  })

  it('clicking Delete all opens ConfirmModal', async () => {
    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    fireEvent.click(screen.getByTestId('overflow-delete-all'))
    expect(screen.getByTestId('confirm-modal-backdrop')).toBeInTheDocument()
    // The modal title contains "Delete all N tasks?"
    expect(screen.getByText(/Delete all \d+ tasks?/i)).toBeInTheDocument()
  })

  it('confirming Delete all calls the delete endpoint with current filters', async () => {
    const mockedApiPostLocal = vi.mocked(api.post)
    mockedApiPostLocal.mockResolvedValue({ deleted: 3, names: ['1', '2', '3'] })

    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    fireEvent.click(screen.getByTestId('overflow-delete-all'))
    // Confirm in the modal
    fireEvent.click(screen.getByTestId('confirm-modal-confirm'))

    await waitFor(() => {
      expect(mockedApiPostLocal).toHaveBeenCalledWith(
        '/tasks/delete-all',
        expect.objectContaining({ status: expect.any(String) })
      )
    })
  })

  it('canceling Delete all does not call the endpoint', async () => {
    const mockedApiPostLocal = vi.mocked(api.post)

    renderTasks()
    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('overflow-menu-trigger'))
    fireEvent.click(screen.getByTestId('overflow-delete-all'))
    // Cancel in the modal
    fireEvent.click(screen.getByTestId('confirm-modal-cancel'))

    expect(screen.queryByTestId('confirm-modal-backdrop')).not.toBeInTheDocument()
    // No /tasks/delete-all call should have been made
    const deleteAllCalls = mockedApiPostLocal.mock.calls.filter(
      ([path]) => path === '/tasks/delete-all'
    )
    expect(deleteAllCalls).toHaveLength(0)
  })

  // ── Counter-vs-list mismatch fix ─────────────────────────────────────
  //
  // Root cause: openCount counted all open (minus session) tasks but
  // ignored priority/label/thread filters. filteredTasks applied every
  // filter. Result: footer showed "6 Open" while the list was empty.
  //
  // Fix: footer derives from visibleCount (= filteredTasks.length) so
  // the counter always matches what is visible. When secondary filters
  // reduce the list to zero, a "N open total · 0 match · Clear filters"
  // hint appears so the user is never stuck wondering where their tasks
  // went.

  describe('counter matches visible list (counter-mismatch fix)', () => {
    it('footer open count matches the visible list when no secondary filters', async () => {
      // "All" default shows 3 open + 1 closed = 4 tasks → footer shows 4
      renderTasks()
      await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())

      const footer = screen.getByTestId('footer-open-count')
      expect(footer).toHaveTextContent('4')
    })

    it('footer open count drops to 0 when a thread filter hides all tasks', async () => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            { id: '1', title: 'Task A', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [], thread_id: null },
            { id: '2', title: 'Task B', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [], thread_id: null },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path === '/threads') return Promise.resolve({ threads: [{ id: 'thread-x', title: 'Thread X' }] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('Task A')).toBeInTheDocument())

      // Switch to Closed tab — zero closed tasks → footer should show 0
      selectOnlyStatus('closed')

      await waitFor(() => {
        const footer = screen.getByTestId('footer-open-count')
        expect(footer).toHaveTextContent('0')
      })
    })

    it('shows "0 match your filters · Clear filters" hint when filters hide all tasks', async () => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            { id: '1', title: 'Open task', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('Open task')).toBeInTheDocument())

      // Switch to Closed tab: the only task is open so list is empty under Closed filter
      selectOnlyStatus('closed')

      // Hint should not appear for a status filter (only secondary filters trigger it),
      // but the footer counter must reflect 0
      await waitFor(() => {
        expect(screen.getByTestId('footer-open-count')).toHaveTextContent('0')
      })
      // The open task must not be visible
      expect(screen.queryByText('Open task')).not.toBeInTheDocument()
    })

    it('clicking "Clear filters" resets thread filter and restores the list', async () => {
      const threadTask1 = { id: '1', title: 'Task in thread', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [], thread_id: 'thread-1' }
      const threadTask2 = { id: '2', title: 'Another task', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [], thread_id: null }

      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: [threadTask1, threadTask2] })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path === '/threads') return Promise.resolve({ threads: [{ id: 'thread-1', title: 'My Thread' }] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('Task in thread')).toBeInTheDocument())
      await waitFor(() => expect(screen.getByText('Another task')).toBeInTheDocument())

      // Both tasks visible, footer shows 2
      expect(screen.getByTestId('footer-open-count')).toHaveTextContent('2')
    })

  })
})

describe('Tasks page - null-priority render fix', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  it('renders tasks with null priority (falls into P3 bucket)', async () => {
    // Tasks 531-536 from ostk arrive with priority=null. Before the fix these
    // were silently dropped from the render loop while still being counted in
    // the footer, so the user saw "6 Open" but an empty list.
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '531', title: 'Unprioritized task A', priority: null, status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '532', title: 'Unprioritized task B', priority: null, status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '533', title: 'Normal P1 task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Normal P1 task')).toBeInTheDocument()
    })

    // Null-priority tasks must appear, not be silently dropped
    expect(screen.getByText('Unprioritized task A')).toBeInTheDocument()
    expect(screen.getByText('Unprioritized task B')).toBeInTheDocument()
  })

  it('footer open count matches the number of visible rows when tasks have null priority', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '531', title: 'Null prio 1', priority: null, status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '532', title: 'Null prio 2', priority: null, status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '533', title: 'Closed task', priority: 'P1', status: 'closed', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Null prio 1')).toBeInTheDocument()
    })

    // "All" default shows 2 open + 1 closed = 3 rows; footer reflects that
    const footer = screen.getByTestId('footer-open-count')
    expect(footer).toHaveTextContent('3')
  })

})

describe('Tasks page - status filter and All toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  describe('default open filter and All toggle', () => {
    const mixedTasks = [
      { id: 'o1', title: 'Open one', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
      { id: 'o2', title: 'Open two', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
      { id: 'c1', title: 'Closed one', priority: 'P2', status: 'closed', created_at: '2024-01-01T00:00:00Z', label_ids: [] },
      { id: 's1', title: 'Shelved one', priority: 'P3', status: 'shelved', created_at: '2024-01-01T00:00:00Z', label_ids: [] },
    ]

    beforeEach(() => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: mixedTasks })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })
    })

    it('on first mount "All" is selected and open+closed rows are both visible', async () => {
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Open one')).toBeInTheDocument()
      })
      expect(screen.getByText('Open two')).toBeInTheDocument()
      // "All" shows closed tasks too
      expect(screen.getByText('Closed one')).toBeInTheDocument()
      // Shelved tasks are always excluded from the pill views
      expect(screen.queryByText('Shelved one')).not.toBeInTheDocument()
    })

    it('clicking Closed pill shows only closed rows (single-select)', async () => {
      // Single-select: clicking Closed switches to closed-only view.
      // Shelved tasks remain outside the pills regardless.
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Open one')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('status-filter-closed'))

      await waitFor(() => {
        expect(screen.getByText('Closed one')).toBeInTheDocument()
      })
      expect(screen.queryByText('Open one')).not.toBeInTheDocument()
      expect(screen.queryByText('Shelved one')).not.toBeInTheDocument()
    })

  })
})

describe('Tasks page - live updates (bus + 3s poll)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders a backend-initiated task within 3.5s without user action', async () => {
    // First call: no tasks. Second call onward: one task present.
    // This simulates a backend-initiated creation (e.g. builder, AC
    // fallback, session-task auto-file) that never touches the frontend
    // api wrapper and therefore never bumps the sidebar bus.
    const emptyTasks: typeof mockTasks = []
    const newTasks = [
      {
        id: 'nt1',
        title: 'Backend added this task',
        priority: 'P1',
        status: 'open',
        created_at: new Date().toISOString(),
        goal: null,
        label_ids: [],
      },
    ]

    let tasksPayload: typeof mockTasks = emptyTasks
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: tasksPayload })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderTasks()

    // Empty state paints first.
    await waitFor(() => {
      expect(screen.queryByText('Backend added this task')).not.toBeInTheDocument()
    })

    // Backend-side creation happens while the user sits on the page.
    tasksPayload = newTasks

    // Advance past the 3s poll interval. The poll tick fires and refetches
    // without the user doing anything.
    await vi.advanceTimersByTimeAsync(3500)

    await waitFor(() => {
      expect(screen.getByText('Backend added this task')).toBeInTheDocument()
    })
  })

  it('does not flash a just-deleted task back into view when the poll refetches', async () => {
    // Regression: the Tasks page polls /tasks every 3s and the delete
    // uses a 5s undo window before calling DELETE. Before the fix, the
    // next poll between click and undo-expiry would return the task
    // (because it still exists on the server) and setTasks clobbered
    // the optimistic removal, flashing the row back on screen for a
    // full second before it disappeared again on timer expiry. The
    // pendingDeleteIdsRef guard filters those ids out of every poll
    // response until the DELETE resolves.
    const stable = [
      { id: 'del1', title: 'Delete me please', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
      { id: 'keep1', title: 'Keep me around', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: stable })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({})

    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Delete me please')).toBeInTheDocument()
    })

    // Scope to the row containing "Delete me please" so we don't hit
    // the sibling task's delete button.
    const row = screen.getByText('Delete me please').closest('[data-testid^="task-row"], [role="listitem"], li, div')!
    const deleteBtn = Array.from(row.querySelectorAll('button')).find(
      (b) => b.getAttribute('title') === 'Delete task permanently'
    ) ?? screen.getAllByTitle('Delete task permanently')[0]
    fireEvent.click(deleteBtn)

    // Task is optimistically removed right away.
    await waitFor(() => {
      expect(screen.queryByText('Delete me please')).not.toBeInTheDocument()
    })

    // Poll fires at 3s. Server still returns the task because DELETE
    // has not yet fired (undo window is 5s). Without the fix, the row
    // would flash back. With the fix, pendingDeleteIdsRef filters it
    // out and the row stays hidden.
    await vi.advanceTimersByTimeAsync(3500)

    expect(screen.queryByText('Delete me please')).not.toBeInTheDocument()
    // Sibling task is unaffected.
    expect(screen.getByText('Keep me around')).toBeInTheDocument()
    // DELETE has not yet fired (timer is 5s, we advanced 3.5s).
    expect(mockedApiDelete).not.toHaveBeenCalled()
  })

  it('shows undo toast after deleting a task and hides it when undo is clicked', async () => {
    const tasks = [
      { id: 'undo1', title: 'Task to undo delete', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null, label_ids: [] },
    ]
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({})

    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Task to undo delete')).toBeInTheDocument()
    })

    const deleteBtn = screen.getByTitle('Delete task permanently')
    fireEvent.click(deleteBtn)

    // Undo toast appears immediately after clicking delete.
    await waitFor(() => {
      expect(screen.getByTestId('undo-delete-task-toast')).toBeInTheDocument()
    })
    expect(screen.getByTestId('undo-delete-task-button')).toBeInTheDocument()

    // Clicking undo restores the task and dismisses the toast.
    fireEvent.click(screen.getByTestId('undo-delete-task-button'))

    await waitFor(() => {
      expect(screen.queryByTestId('undo-delete-task-toast')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Task to undo delete')).toBeInTheDocument()
    // DELETE should not have been called since we undid.
    expect(mockedApiDelete).not.toHaveBeenCalled()
  })
})

// ───────────────────────────────────────────────────────────────
// Regression tests for the 2026-04-23 Tasks-page cleanup: card view,
// in-progress indicator, duplicate sort row, three-pill multi-select,
// effective status for tasks with a running agent.
// ───────────────────────────────────────────────────────────────
describe('Tasks page - 2026-04-23 regression set', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  it('does not render the legacy card-view / view-toggle / clear-all toolbar', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByTestId('filter-drawer')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('view-mode-list')).not.toBeInTheDocument()
    expect(screen.queryByTestId('view-mode-grid')).not.toBeInTheDocument()
    expect(screen.queryByTestId('view-mode-toggle')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-drawer-close')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })

  it('filter drawer renders exactly one sort row (no duplicate SORT bar)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByTestId('filter-drawer-sort-row')).toBeInTheDocument()
    })
    expect(screen.getAllByTestId('filter-drawer-sort-row')).toHaveLength(1)
    // Legacy closed-sort toggle testids must stay gone.
    expect(screen.queryByTestId('closed-sort-newest')).not.toBeInTheDocument()
    expect(screen.queryByTestId('closed-sort-oldest')).not.toBeInTheDocument()
  })

  it('exposes exactly four status pills (All / Open / In progress / Closed) with aria-pressed', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByTestId('status-filter-all')).toBeInTheDocument()
    })
    expect(screen.getByTestId('status-filter-open')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-in_progress')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-closed')).toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-shelved')).not.toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-week')).not.toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-recurring')).not.toBeInTheDocument()
    // Default: All selected, others off.
    expect(screen.getByTestId('status-filter-all')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('status-filter-open')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByTestId('status-filter-in_progress')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByTestId('status-filter-closed')).toHaveAttribute('aria-pressed', 'false')
  })

  it('multi-select: clicking Closed adds it, last remaining pill cannot be deselected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: 'o', title: 'Open task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: 'c', title: 'Closed task', priority: 'P1', status: 'closed', created_at: new Date().toISOString(), closed_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByText('Open task')).toBeInTheDocument()
    })
    // Click Closed → now open + in_progress + closed are selected.
    fireEvent.click(screen.getByTestId('status-filter-closed'))
    expect(screen.getByTestId('status-filter-closed')).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => {
      expect(screen.getByText('Closed task')).toBeInTheDocument()
    })

    // Deselect open, then in_progress; last remaining (closed) must stay on.
    fireEvent.click(screen.getByTestId('status-filter-open'))
    fireEvent.click(screen.getByTestId('status-filter-in_progress'))
    fireEvent.click(screen.getByTestId('status-filter-closed'))
    expect(screen.getByTestId('status-filter-closed')).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows the in-progress indicator on a task row when an agent is running on it', async () => {
    const task = { id: 'a123', title: 'Task with agent', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [task] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/agents') return Promise.resolve({ agents: [{ status: 'running', task_id: 'a123' }] })
      return Promise.resolve({})
    })
    renderTasks()
    await waitFor(() => {
      expect(screen.getByTestId('task-in-progress-indicator-a123')).toBeInTheDocument()
    })
    expect(screen.getByTestId('task-row-a123')).toHaveAttribute('data-in-progress', 'true')
  })

  it('effective status: a task whose stored status is open but has a running agent counts under In Progress', async () => {
    const task = { id: 'eff1', title: 'Agent-backed open task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [task] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/agents') return Promise.resolve({ agents: [{ status: 'running', task_id: 'eff1' }] })
      return Promise.resolve({})
    })
    renderTasks()
    // Wait for the agent poll to apply.
    await waitFor(() => {
      expect(screen.getByTestId('task-in-progress-indicator-eff1')).toBeInTheDocument()
    })
    // Select the In Progress pill; the task must appear (effective status = in_progress).
    fireEvent.click(screen.getByTestId('status-filter-in_progress'))
    expect(screen.getByText('Agent-backed open task')).toBeInTheDocument()
    // Flip to Open-only: effective status is in_progress so the task must disappear.
    fireEvent.click(screen.getByTestId('status-filter-open'))
    expect(screen.queryByText('Agent-backed open task')).not.toBeInTheDocument()
    // Flip to Closed-only: task also disappears.
    selectOnlyStatus('closed')
    expect(screen.queryByText('Agent-backed open task')).not.toBeInTheDocument()
  })

  describe('default sort: in-progress first, then newest', () => {
    it('in-progress task renders before open task regardless of created_at order', async () => {
      const tasks = [
        { id: 'old1', title: 'Old open task', priority: 'P1', status: 'open', created_at: '2026-01-01T00:00:00Z', goal: null, label_ids: [] },
        { id: 'new2', title: 'New in-progress task', priority: 'P1', status: 'open', created_at: '2024-01-01T00:00:00Z', goal: null, label_ids: [] },
      ]
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path === '/agents') return Promise.resolve({ agents: [{ status: 'running', task_id: 'new2' }] })
        return Promise.resolve({})
      })
      renderTasks()
      await waitFor(() => {
        expect(screen.getByTestId('task-in-progress-indicator-new2')).toBeInTheDocument()
      })
      const body = document.body.textContent || ''
      expect(body.indexOf('New in-progress task')).toBeLessThan(body.indexOf('Old open task'))
    })

    it('two open tasks render in newest-first order', async () => {
      const tasks = [
        { id: 'older', title: 'Older open task', priority: 'P1', status: 'open', created_at: '2024-03-01T00:00:00Z', goal: null, label_ids: [] },
        { id: 'newer', title: 'Newer open task', priority: 'P1', status: 'open', created_at: '2026-03-01T00:00:00Z', goal: null, label_ids: [] },
      ]
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path === '/agents') return Promise.resolve({ agents: [] })
        return Promise.resolve({})
      })
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Newer open task')).toBeInTheDocument()
      })
      const body = document.body.textContent || ''
      expect(body.indexOf('Newer open task')).toBeLessThan(body.indexOf('Older open task'))
    })
  })

  describe('in-progress badge is runtime-derived only (regression: stored in_progress must not show badge)', () => {
    it('stored in_progress task with no running agent: visible in Open tab, no badge, absent from In Progress tab', async () => {
      const task = {
        id: 'legacy1',
        title: 'Legacy in-progress task',
        priority: 'P1',
        status: 'in_progress',
        created_at: new Date().toISOString(),
        goal: null,
        label_ids: [],
      }
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({ tasks: [task] })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        if (path === '/agents') return Promise.resolve({ agents: [] })
        return Promise.resolve({})
      })
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Legacy in-progress task')).toBeInTheDocument()
      })
      // No badge — no agent is running on this task
      expect(screen.queryByTestId('task-in-progress-indicator-legacy1')).not.toBeInTheDocument()
      // Task is visible in the default view (stored in_progress maps to effective open)
      expect(screen.getByTestId('task-row-legacy1')).toBeInTheDocument()
      // Select ONLY In Progress — task must NOT appear (no running agent)
      selectOnlyStatus('in_progress')
      await waitFor(() => {
        expect(screen.queryByText('Legacy in-progress task')).not.toBeInTheDocument()
      })
    })
  })
})