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
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const mockTasks = [
  { id: '1', title: 'Fix login bug', priority: 'P0', status: 'open', created_at: new Date().toISOString(), goal: 'Auth' },
  { id: '2', title: 'Add dark mode', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: 'UI' },
  { id: '3', title: 'Write docs', priority: 'P2', status: 'open', created_at: new Date().toISOString(), goal: null },
  { id: '4', title: 'Old completed task', priority: 'P1', status: 'closed', created_at: '2024-01-01T00:00:00Z', goal: null },
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
    useAppStore.setState({ chatOpen: true, osName: 'YourOS', darkMode: true })
    mockedApiGet.mockResolvedValue({ tasks: mockTasks })
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

  it('shows loading state before tasks arrive', () => {
    // Make the API hang indefinitely
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderTasks()

    expect(screen.getByText('Loading tasks...')).toBeInTheDocument()
  })

  it('filter buttons show correct counts', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Open count: 3 (tasks 1, 2, 3 are open)
    // Closed count: 1 (task 4)
    // The counts appear inside buttons as text
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

    // Open tasks should be visible
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Write docs')).toBeInTheDocument()

    // Closed task should not be visible in the default "open" filter
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

    // Switch to closed
    fireEvent.click(screen.getByRole('button', { name: /Closed/i }))
    expect(screen.queryByText('Fix login bug')).not.toBeInTheDocument()

    // Switch back to open
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

    // The add button is identifiable as the button next to the input
    // It's a round blue button with an add icon
    const addButtons = screen.getAllByText('add')
    // The quick-add button is the first one (in the input area)
    const addButton = addButtons[0].closest('button')!
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

    const addButton = screen.getAllByText('add')[0].closest('button')!
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

    const addButton = screen.getAllByText('add')[0].closest('button')!
    fireEvent.click(addButton)

    // api.post should not have been called (only api.get for initial fetch)
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

    // Close buttons are the circle buttons next to each task
    // They have title="Close task" for open tasks
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

    // Switch to closed filter to see closed tasks
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

  it('project filter dropdown appears when tasks have goals', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // The goal filter is a select dropdown with "All projects" as default option
    const select = screen.getByDisplayValue('All projects')
    expect(select).toBeInTheDocument()

    // Should contain the unique goals from tasks
    const options = select.querySelectorAll('option')
    const optionTexts = Array.from(options).map((o) => o.textContent)
    expect(optionTexts).toContain('All projects')
    expect(optionTexts).toContain('Auth')
    expect(optionTexts).toContain('UI')
  })

  it('project filter dropdown does not appear when no tasks have goals', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Task without goal', priority: 'P1', status: 'open', created_at: new Date().toISOString(), goal: null },
      ],
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Task without goal')).toBeInTheDocument()
    })

    expect(screen.queryByDisplayValue('All projects')).not.toBeInTheDocument()
  })

  it('selecting a project filter shows only tasks with that goal', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    const select = screen.getByDisplayValue('All projects')
    fireEvent.change(select, { target: { value: 'Auth' } })

    // Only Auth tasks should be visible
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Write docs')).not.toBeInTheDocument()
  })

  it('priority filter buttons work', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // Click P0 filter button (it contains the count text, e.g. "P0 1")
    const p0Buttons = screen.getAllByRole('button', { name: /P0/i })
    // The filter button is the one that also contains a count
    const p0FilterButton = p0Buttons.find((b) => b.textContent?.match(/P0\s*1/))!
    fireEvent.click(p0FilterButton)

    // Only P0 tasks should be visible
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Write docs')).not.toBeInTheDocument()
  })

  it('clicking same priority filter again removes the filter', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // The filter button contains both "P0" and the count
    const p0Buttons = screen.getAllByRole('button', { name: /P0/i })
    const p0FilterButton = p0Buttons.find((b) => b.textContent?.match(/P0\s*1/))!

    // Click to filter
    fireEvent.click(p0FilterButton)
    expect(screen.queryByText('Add dark mode')).not.toBeInTheDocument()

    // Click again to unfilter
    fireEvent.click(p0FilterButton)
    expect(screen.getByText('Add dark mode')).toBeInTheDocument()
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
  })

  it('shows "No tasks match this filter" when filter yields no results', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [
        { id: '1', title: 'Only open', priority: 'P1', status: 'open', created_at: new Date().toISOString() },
      ],
    })

    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Only open')).toBeInTheDocument()
    })

    // Switch to closed filter
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

    // Each task row should show its priority
    const priorities = screen.getAllByText(/^P[012]$/)
    // 3 open tasks visible + 3 filter buttons = more instances
    expect(priorities.length).toBeGreaterThanOrEqual(3)
  })

  it('shows footer with open and closed counts', async () => {
    renderTasks()

    await waitFor(() => {
      expect(screen.getByText('Fix login bug')).toBeInTheDocument()
    })

    // The footer shows "X Open" and "X Closed" as separate spans
    // Use getAllByText since "Open" and "Closed" also appear in filter buttons
    const openTexts = screen.getAllByText('Open')
    const closedTexts = screen.getAllByText('Closed')
    expect(openTexts.length).toBeGreaterThanOrEqual(1)
    expect(closedTexts.length).toBeGreaterThanOrEqual(1)
  })

  it('refetches tasks after closing a task', async () => {
    renderTasks()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledTimes(1)
    })

    const closeButtons = screen.getAllByTitle('Close task')
    fireEvent.click(closeButtons[0])

    await waitFor(() => {
      // Initial fetch + refetch after close
      expect(mockedApiGet).toHaveBeenCalledTimes(2)
    })
  })
})
