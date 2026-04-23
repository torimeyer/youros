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

describe('Tasks page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint tasks cache between tests so state does
    // not leak from one test to the next. Needle 299.
    window.localStorage.clear()
    // Clear the per-session "show session tasks" preference so each
    // test starts with the default behavior (sessions hidden).
    window.sessionStorage.clear()
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

  it('defaults to showing open tasks (hides closed)', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Write docs')).toBeInTheDocument()
    expect(screen.queryByText('Old completed task')).not.toBeInTheDocument()
  })

  it('clicking Closed filter shows only closed tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('status-filter-closed'))

    expect(screen.getByText('Old completed task')).toBeInTheDocument()
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
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
      // Wait for data to load — switch to closed filter first
      })
    fireEvent.click(screen.getByTestId('status-filter-closed'))

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

  it('closed-sort toggle: switching to oldest-first reverses the task order', async () => {
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

    // Open filters and switch to closed view
    await waitFor(() => {
      })
    fireEvent.click(screen.getByTestId('status-filter-closed'))

    // Default order is newest-first — newest appears before oldest in the DOM
    await waitFor(() => {
      expect(screen.getByText('Closed newest')).toBeInTheDocument()
    })

    const newestFirstBody = document.body.textContent || ''
    const defaultPositions = {
      newest: newestFirstBody.indexOf('Closed newest'),
      oldest: newestFirstBody.indexOf('Closed oldest'),
    }
    expect(defaultPositions.newest).toBeLessThan(defaultPositions.oldest)

    // Sort toggle buttons should be visible
    expect(screen.getByTestId('closed-sort-newest')).toBeInTheDocument()
    expect(screen.getByTestId('closed-sort-oldest')).toBeInTheDocument()

    // Click "Oldest first"
    fireEvent.click(screen.getByTestId('closed-sort-oldest'))

    await waitFor(() => {
      const oldestFirstBody = document.body.textContent || ''
      const reversedPositions = {
        newest: oldestFirstBody.indexOf('Closed newest'),
        oldest: oldestFirstBody.indexOf('Closed oldest'),
      }
      expect(reversedPositions.oldest).toBeLessThan(reversedPositions.newest)
    })
  })

  it('clicking Open filter after Closed shows open tasks again', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('status-filter-closed'))
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('status-filter-open'))
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

  it('shows label filter chips in the filter drawer', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open filter drawer to see label chips
    expect(screen.getByTestId('label-filter-l1')).toBeInTheDocument()
    expect(screen.getByTestId('label-filter-l2')).toBeInTheDocument()
  })

  it('clicking a label filter chip filters tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open filter drawer and click the Bug label chip
    fireEvent.click(screen.getByTestId('label-filter-l1'))

    // Tasks with "Bug" label (l1): task 1 and task 3
    // Task 2 (Add dark mode, no label) should be hidden
    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      expect(screen.getByText('Write docs')).toBeInTheDocument()
      expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
    })
  })

  it('priority filter buttons work', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open filter drawer and click P0
    fireEvent.click(screen.getByTestId('priority-filter-P0'))

    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Write docs')).not.toBeInTheDocument()
  })

  it('clicking same priority filter again removes the filter', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('priority-filter-P0'))
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('priority-filter-P0'))
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
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

    fireEvent.click(screen.getByTestId('status-filter-closed'))
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

  it('shows Context and History tabs in the briefing panel', async () => {
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

    // Should see Context and History tab buttons
    expect(screen.getByRole('button', { name: 'Context' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument()
  })

  // --- History / Trace panel ---

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

  it('clicking History tab shows the trace panel with commits', async () => {
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
      expect(screen.getByTestId('history-tab')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('history-tab'))

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

  it('History tab shows empty message when trace has no data', async () => {
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
      expect(screen.getByTestId('history-tab')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('history-tab'))

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

      // Default filter is "open". All three active tasks must be visible,
      // the closed one must not.
      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })
      expect(screen.getByText('Active in-progress one')).toBeInTheDocument()
      expect(screen.getByText('Active in-progress two')).toBeInTheDocument()
      expect(screen.queryByText('Done dusted and gone')).not.toBeInTheDocument()
    })

    it('Open count badge includes in_progress tasks', async () => {
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })
      // Open filter drawer to access status filter buttons.
      // Before needle 277 fix this badge read "1" instead of "3".
        const openButton = screen.getByTestId('status-filter-open')
      expect(openButton).toHaveTextContent('3')
    })

    it('Closed tab still only shows closed tasks', async () => {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Active open task')).toBeInTheDocument()
      })

        fireEvent.click(screen.getByTestId('status-filter-closed'))

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
    window.sessionStorage.clear()
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

  // --- Session task filter (Bug 3) ---

  it('hides session tasks by default (old hook format with "Claude Code session" title)', async () => {
    const sessionTask = {
      id: '99',
      title: 'Claude Code session claude-code-abc12345',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'Auto-filed by SessionStart hook. Close when the session\'s work is merged or abandoned.',
      label_ids: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [...mockTasks, sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Session task should be hidden by default
    expect(screen.queryByText('Claude Code session claude-code-abc12345')).not.toBeInTheDocument()
    // Normal tasks should still be visible
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
  })

  it('hides session tasks by default (new hook format with session-task: description)', async () => {
    const sessionTask = {
      id: '98',
      title: 'Claude session abc12345 (2:30pm)',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Auto-filed when this Claude Code session started.',
      label_ids: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [...mockTasks, sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Session task should be hidden by default
    expect(screen.queryByText('Claude session abc12345 (2:30pm)')).not.toBeInTheDocument()
  })

  it('shows session tasks when the toggle is clicked', async () => {
    const sessionTask = {
      id: '97',
      title: 'Claude session xyz99999 (3:15pm)',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Auto-filed when this Claude Code session started.',
      label_ids: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [...mockTasks, sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Hidden by default
    expect(screen.queryByText('Claude session xyz99999 (3:15pm)')).not.toBeInTheDocument()

    // Open filter drawer, then click the sessions toggle
    const toggleBtn = screen.getByTestId('toggle-session-tasks')
    fireEvent.click(toggleBtn)

    // Now the session task should appear
    expect(screen.getByText('Claude session xyz99999 (3:15pm)')).toBeInTheDocument()
  })

  it('tasks page session filter defaults off (sessions hidden) on every fresh mount', async () => {
    // Tori asked: opening the page or reloading should ALWAYS hide
    // session tasks unless the user flipped the toggle in this same
    // browser tab. This guards against a regression where state
    // persisted in localStorage made sessions show up forever.
    const sessionTask = {
      id: '98',
      title: 'Session in torios',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Auto-filed when this Claude Code session started.',
      label_ids: [],
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [...mockTasks, sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    // Pretend a previous tab had wired sessionStorage to a stale state.
    // A reload (which clears sessionStorage) MUST come back hidden.
    window.sessionStorage.removeItem('myos.tasks.showSessionTasks')

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    expect(screen.queryByText('Session in torios')).not.toBeInTheDocument()

    // After mount, sessionStorage should reflect the hidden default
    // ("0" means hide). This pins the persistence direction.
    expect(window.sessionStorage.getItem('myos.tasks.showSessionTasks')).toBe('0')
  })
})

describe('Tasks page - simplified toolbar (2-layer layout)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
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
      // 3 open tasks, no filters → footer should show 3
      renderTasks()
      await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())

      const footer = screen.getByTestId('footer-open-count')
      expect(footer).toHaveTextContent('3')
    })

    it('footer open count drops to 0 when a P0 priority filter is set and no P0 tasks match', async () => {
      // Only task 1 is P0. After P1 filter there are 0 visible P1 tasks... actually
      // let us use a label filter that matches nothing.
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            { id: '1', title: 'Session task', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
            { id: '2', title: 'Regular task', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('Regular task')).toBeInTheDocument())

      // Apply P0 filter (no P0 tasks exist)
        fireEvent.click(screen.getByTestId('priority-filter-P0'))

      // List is empty
      expect(screen.queryByText('Regular task')).not.toBeInTheDocument()
      expect(screen.queryByText('Session task')).not.toBeInTheDocument()

      // Footer counter must reflect 0
      const footer = screen.getByTestId('footer-open-count')
      expect(footer).toHaveTextContent('0')
    })

    it('shows "0 match your filters · Clear filters" hint when filters hide all tasks', async () => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            { id: '1', title: 'P2 task', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('P2 task')).toBeInTheDocument())

      // Apply P0 filter (hides the only open task)
        fireEvent.click(screen.getByTestId('priority-filter-P0'))

      // Hint should appear
      await waitFor(() => {
        expect(screen.getByTestId('filters-hiding-hint')).toBeInTheDocument()
      })
      expect(screen.getByTestId('clear-filters-btn')).toBeInTheDocument()
    })

    it('clicking "Clear filters" resets priority/label/thread filters and restores the list', async () => {
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            { id: '1', title: 'P2 task A', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
            { id: '2', title: 'P2 task B', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })

      renderTasks()
      await waitFor(() => expect(screen.getByText('P2 task A')).toBeInTheDocument())

      // Apply P0 filter
        fireEvent.click(screen.getByTestId('priority-filter-P0'))

      // Wait for hint
      await waitFor(() => {
        expect(screen.getByTestId('clear-filters-btn')).toBeInTheDocument()
      })

      // Click "Clear filters"
      fireEvent.click(screen.getByTestId('clear-filters-btn'))

      // Tasks reappear
      await waitFor(() => {
        expect(screen.getByText('P2 task A')).toBeInTheDocument()
        expect(screen.getByText('P2 task B')).toBeInTheDocument()
      })

      // Hint is gone
      expect(screen.queryByTestId('filters-hiding-hint')).not.toBeInTheDocument()

      // Footer shows 2
      expect(screen.getByTestId('footer-open-count')).toHaveTextContent('2')
    })

    it('shows session-hiding empty state (not generic filter hint) when sessions are the only open tasks', async () => {
      // When every open task is a session task and hideSessionTasks=true, the
      // session-hiding empty state must appear, NOT the generic "No tasks match
      // this filter." text, and NOT the secondary-filter hint (filters-hiding-hint).
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/tasks') return Promise.resolve({
          tasks: [
            {
              id: '1',
              title: 'Claude Code session claude-code-abc',
              priority: 'P1',
              status: 'open',
              created_at: new Date().toISOString(),
              description: 'session-task: boot',
              label_ids: [],
            },
          ],
        })
        if (path === '/labels') return Promise.resolve({ labels: [] })
        return Promise.resolve({})
      })

      renderTasks()

      // Session-hiding empty state must appear
      await waitFor(() => {
        expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
      })

      // The old generic message must NOT appear
      expect(screen.queryByText('No tasks match this filter.')).not.toBeInTheDocument()
      // The secondary-filter hint must NOT appear (no priority/label/thread filter active)
      expect(screen.queryByTestId('filters-hiding-hint')).not.toBeInTheDocument()
      // The footer session hint must appear
      expect(screen.getByTestId('session-hiding-hint')).toBeInTheDocument()
    })
  })
})

describe('Tasks page - null-priority render fix', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
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

    // Footer must say 2 open (matching the 2 visible rows), not 0
    const footer = screen.getByTestId('footer-open-count')
    expect(footer).toHaveTextContent('2')
  })

  it('session-only open tasks: counter shows 0 Open and session-hiding hint appears', async () => {
    // All open tasks are session tasks. Counter must say 0, list is empty,
    // and the session-hiding hint must explain why with a "Show sessions" button.
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '537', title: 'Claude Code session claude-code-abc123', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '538', title: 'Claude Code session claude-code-def456', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '400', title: 'Done task', priority: 'P1', status: 'closed', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
    })

    // Counter must show 0, not the raw session count
    const footer = screen.getByTestId('footer-open-count')
    expect(footer).toHaveTextContent('0')

    // Footer session hint must be present
    expect(screen.getByTestId('session-hiding-hint')).toBeInTheDocument()

    // Session tasks must not be visible yet
    expect(screen.queryByText('Claude Code session claude-code-abc123')).not.toBeInTheDocument()
  })

  it('clicking "Show sessions" in the empty state reveals session tasks', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '537', title: 'Claude Code session claude-code-abc123', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('show-sessions-btn')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('show-sessions-btn'))

    // After clicking, session tasks appear and counter updates to 1
    await waitFor(() => {
      expect(screen.getByText('Claude Code session claude-code-abc123')).toBeInTheDocument()
    })
    const footer = screen.getByTestId('footer-open-count')
    expect(footer).toHaveTextContent('1')
  })

  it('mix of session and regular tasks: counter shows only regular count when sessions hidden', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({
        tasks: [
          { id: '531', title: 'Real task 1', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '532', title: 'Real task 2', priority: 'P2', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
          { id: '537', title: 'Claude Code session claude-code-abc123', priority: 'P1', status: 'open', created_at: new Date().toISOString(), label_ids: [] },
        ],
      })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Real task 1')).toBeInTheDocument()
    })

    // Regular tasks visible, session task hidden
    expect(screen.getByText('Real task 2')).toBeInTheDocument()
    expect(screen.queryByText('Claude Code session claude-code-abc123')).not.toBeInTheDocument()

    // Counter shows 2 (not 3)
    const footer = screen.getByTestId('footer-open-count')
    expect(footer).toHaveTextContent('2')

    // No session-hiding empty state (list is not empty)
    expect(screen.queryByTestId('session-hiding-empty-state')).not.toBeInTheDocument()
  })
})

describe('Tasks page - session transcript link and child task count', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiPost.mockResolvedValue({})
  })

  it('renders a View transcript button on a session task row', async () => {
    const sessionTask = {
      id: '99',
      title: 'Session in torios',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Work happening in /Users/torimeyer/claude/torios.',
      label_ids: [],
      session_id: 'sess-abc-123',
      child_task_count: 3,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    // Session tasks are hidden by default. Flip the toggle first so the row renders.
    await waitFor(() => {
      expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('show-sessions-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('view-transcript-99')).toBeInTheDocument()
    })
    expect(screen.getByTestId('view-transcript-99')).toHaveTextContent(/View transcript/i)
  })

  it('renders a "N tasks created in this session" count when child_task_count > 0', async () => {
    const sessionTask = {
      id: '100',
      title: 'Session in torios',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Work happening in /Users/torimeyer/claude/torios.',
      label_ids: [],
      session_id: 'sess-abc-123',
      child_task_count: 3,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('show-sessions-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('child-task-count-100')).toBeInTheDocument()
    })
    expect(screen.getByTestId('child-task-count-100')).toHaveTextContent(/3 tasks created in this session/i)
  })

  it('hides the child task count when child_task_count is 0', async () => {
    const sessionTask = {
      id: '101',
      title: 'Session in torios',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Work happening in /Users/torimeyer/claude/torios.',
      label_ids: [],
      session_id: 'sess-abc-123',
      child_task_count: 0,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('show-sessions-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('view-transcript-101')).toBeInTheDocument()
    })
    // No child count row renders when count is zero.
    expect(screen.queryByTestId('child-task-count-101')).not.toBeInTheDocument()
  })

  it('does NOT render View transcript on a task with no session_id', async () => {
    const regularTask = {
      id: '102',
      title: 'Fix login bug',
      priority: 'P1',
      status: 'open',
      created_at: new Date().toISOString(),
      label_ids: [],
      session_id: null,
      child_task_count: 0,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [regularTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('view-transcript-102')).not.toBeInTheDocument()
  })

  it('singularizes the child task count to "1 task created" when count is 1', async () => {
    const sessionTask = {
      id: '103',
      title: 'Session in torios',
      priority: 'P3',
      status: 'open',
      created_at: new Date().toISOString(),
      description: 'session-task: Work happening in /Users/torimeyer/claude/torios.',
      label_ids: [],
      session_id: 'sess-xyz',
      child_task_count: 1,
    }
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks') return Promise.resolve({ tasks: [sessionTask] })
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByTestId('session-hiding-empty-state')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('show-sessions-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('child-task-count-103')).toBeInTheDocument()
    })
    expect(screen.getByTestId('child-task-count-103')).toHaveTextContent(/1 task created in this session/i)
    // Not pluralized
    expect(screen.getByTestId('child-task-count-103')).not.toHaveTextContent(/1 tasks/i)
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

    it('on first mount only open rows are rendered', async () => {
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Open one')).toBeInTheDocument()
      })
      expect(screen.getByText('Open two')).toBeInTheDocument()
      expect(screen.queryByText('Closed one')).not.toBeInTheDocument()
      expect(screen.queryByText('Shelved one')).not.toBeInTheDocument()
    })

    it('selecting All in the filter drawer reveals previously hidden rows', async () => {
      renderTasks()

      await waitFor(() => {
        expect(screen.getByText('Open one')).toBeInTheDocument()
      })
        fireEvent.click(screen.getByTestId('status-filter-all'))

      await waitFor(() => {
        expect(screen.getByText('Closed one')).toBeInTheDocument()
      })
      expect(screen.getByText('Shelved one')).toBeInTheDocument()
      expect(screen.getByText('Open one')).toBeInTheDocument()
    })

  })
})

describe('Tasks page - live updates (bus + 3s poll)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
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