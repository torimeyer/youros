import { describe, it, expect, vi, beforeEach } from 'vitest'
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

    expect(screen.getByText('Loading tasks...')).toBeInTheDocument()
  })

  it('renders the export button in the toolbar', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    expect(screen.getByTestId('export-button')).toBeInTheDocument()
  })

  it('filter buttons show correct counts', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const openButton = screen.getByRole('button', { name: /Open/i })
    expect(openButton).toHaveTextContent('3')

    const closedButton = screen.getByRole('button', { name: /Closed/i })
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

    const closedButton = screen.getByRole('button', { name: /Closed/i })
    fireEvent.click(closedButton)

    expect(screen.getByText('Old completed task')).toBeInTheDocument()
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
  })

  it('clicking Open filter after Closed shows open tasks again', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Closed/i }))
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Open/i }))
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

    fireEvent.click(screen.getByRole('button', { name: /Closed/i }))

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

  it('shows label filter chips in the filter bar', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Label filter chips should be in the filter bar
    // "Bug" and "Docs" labels appear as filter chips
    const bugChips = screen.getAllByRole('button', { name: /Bug/ })
    expect(bugChips.length).toBeGreaterThanOrEqual(1)
  })

  it('clicking a label filter chip filters tasks', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Find the "Bug" filter chip button in the filter bar (not inside a task row)
    // The filter bar label chip has a colored dot and the label name
    const bugFilterButtons = screen.getAllByRole('button', { name: /Bug/ })
    // Click the first one that is a filter chip (outside task rows)
    fireEvent.click(bugFilterButtons[0])

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

    const p0Buttons = screen.getAllByRole('button', { name: /P0/i })
    const p0FilterButton = p0Buttons.find((b) => b.textContent?.match(/P0\s*1/))!
    fireEvent.click(p0FilterButton)

    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Write docs')).not.toBeInTheDocument()
  })

  it('clicking same priority filter again removes the filter', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const p0Buttons = screen.getAllByRole('button', { name: /P0/i })
    const p0FilterButton = p0Buttons.find((b) => b.textContent?.match(/P0\s*1/))!

    fireEvent.click(p0FilterButton)
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()

    fireEvent.click(p0FilterButton)
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

    fireEvent.click(screen.getByRole('button', { name: /Closed/i }))
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
      const closedButton = screen.getByRole('button', { name: /Closed/i })
      fireEvent.click(closedButton)
    })

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

    it('renders an auto indicator on auto-applied labels', async () => {
      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })
      expect(screen.getByTestId('auto-icon-1-l1')).toBeInTheDocument()
    })

    it('clicking an auto-applied label removes it via the API', async () => {
      const mockedDelete = vi.mocked(api.delete)
      mockedDelete.mockResolvedValue({ label_ids: [] })

      renderTasks()
      await waitFor(() => {
        expect(screen.getByText('Fix login bug')).toBeInTheDocument()
      })

      const autoIcon = screen.getByTestId('auto-icon-1-l1')
      const pill = autoIcon.closest('span[class*="rounded-full"]') as HTMLElement
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

  // Suggestions UI removed (→249). Tests will be restored when the feature is polished.
})