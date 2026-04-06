import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { CommandPalette } from './CommandPalette'
import { useAppStore } from '../stores/app'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockedApiGet = vi.mocked(api.get)

function renderPalette(open = true, onClose = vi.fn()) {
  return {
    onClose,
    ...render(
      <MemoryRouter>
        <CommandPalette open={open} onClose={onClose} />
      </MemoryRouter>
    ),
  }
}

describe('CommandPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true })
    mockedApiGet.mockResolvedValue({ tasks: [], ideas: [], query: '' })
  })

  it('renders nothing when open is false', () => {
    renderPalette(false)
    expect(screen.queryByTestId('command-palette')).not.toBeInTheDocument()
  })

  it('renders the modal when open is true', () => {
    renderPalette(true)
    expect(screen.getByTestId('command-palette')).toBeInTheDocument()
  })

  it('renders a search input with placeholder', () => {
    renderPalette()
    expect(screen.getByTestId('command-palette-input')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Type a command or search...')).toBeInTheDocument()
  })

  it('renders all navigation commands', () => {
    renderPalette()
    const pages = ['Home', 'Tasks', 'Ideas', 'Agents', 'Timeline', 'Files', 'Transcripts', 'Settings']
    for (const page of pages) {
      expect(screen.getByText(`Go to ${page}`)).toBeInTheDocument()
    }
  })

  it('renders action commands', () => {
    renderPalette()
    expect(screen.getByText('Toggle Chat')).toBeInTheDocument()
    expect(screen.getByText('Create New Task')).toBeInTheDocument()
  })

  it('renders section headers', () => {
    renderPalette()
    expect(screen.getByText('Navigate')).toBeInTheDocument()
    expect(screen.getByText('Actions')).toBeInTheDocument()
  })

  it('filters commands when typing', async () => {
    const user = userEvent.setup()
    renderPalette()

    const input = screen.getByTestId('command-palette-input')
    await user.type(input, 'task')

    expect(screen.getByText('Go to Tasks')).toBeInTheDocument()
    expect(screen.getByText('Create New Task')).toBeInTheDocument()
    expect(screen.queryByText('Go to Home')).not.toBeInTheDocument()
    expect(screen.queryByText('Go to Ideas')).not.toBeInTheDocument()
  })

  it('shows empty state when no commands match and no search results', async () => {
    const user = userEvent.setup()
    mockedApiGet.mockResolvedValue({ tasks: [], ideas: [], query: 'xyznonexistent' })
    renderPalette()

    const input = screen.getByTestId('command-palette-input')
    await user.type(input, 'xyznonexistent')

    await waitFor(() => {
      expect(screen.getByText('No matching commands')).toBeInTheDocument()
    })
  })

  it('calls onClose when Escape is pressed', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    const input = screen.getByTestId('command-palette-input')
    fireEvent.keyDown(input, { key: 'Escape' })

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when clicking the backdrop', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-palette-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not call onClose when clicking inside the modal', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-palette'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('navigates to correct page when clicking a nav command', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-item-nav-tasks'))

    expect(mockNavigate).toHaveBeenCalledWith('/tasks')
    expect(onClose).toHaveBeenCalled()
  })

  it('navigates home when clicking Go to Home', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-item-nav-home'))

    expect(mockNavigate).toHaveBeenCalledWith('/')
    expect(onClose).toHaveBeenCalled()
  })

  it('toggles chat when clicking Toggle Chat', () => {
    const onClose = vi.fn()
    useAppStore.setState({ chatOpen: false })
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-item-action-chat'))
    expect(onClose).toHaveBeenCalled()
    // Chat should now be toggled
    expect(useAppStore.getState().chatOpen).toBe(true)
  })

  it('navigates to tasks with new param when clicking Create New Task', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    fireEvent.click(screen.getByTestId('command-item-action-new-task'))
    expect(mockNavigate).toHaveBeenCalledWith('/tasks?new=1')
    expect(onClose).toHaveBeenCalled()
  })

  it('executes the selected command on Enter', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    const input = screen.getByTestId('command-palette-input')
    // First item (Go to Home) should be selected by default
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mockNavigate).toHaveBeenCalledWith('/')
    expect(onClose).toHaveBeenCalled()
  })

  it('moves selection down with ArrowDown', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    const input = screen.getByTestId('command-palette-input')
    // Move down once to select "Go to Tasks"
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mockNavigate).toHaveBeenCalledWith('/tasks')
  })

  it('moves selection up with ArrowUp', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    const input = screen.getByTestId('command-palette-input')
    // ArrowUp from first item should wrap to last item (Create New Task)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mockNavigate).toHaveBeenCalledWith('/tasks?new=1')
  })

  it('wraps selection from last to first with ArrowDown', () => {
    const onClose = vi.fn()
    renderPalette(true, onClose)

    const input = screen.getByTestId('command-palette-input')
    // 10 total commands. Pressing ArrowDown 10 times wraps back to index 0.
    for (let i = 0; i < 10; i++) {
      fireEvent.keyDown(input, { key: 'ArrowDown' })
    }
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mockNavigate).toHaveBeenCalledWith('/')
  })

  it('displays keyboard shortcuts for action commands', () => {
    renderPalette()
    // The Toggle Chat command has shortcut key label
    const chatItem = screen.getByTestId('command-item-action-chat')
    expect(chatItem.textContent).toContain('⌘L')
  })

  it('filters by section name', async () => {
    const user = userEvent.setup()
    renderPalette()

    const input = screen.getByTestId('command-palette-input')
    await user.type(input, 'actions')

    // Only action commands should remain
    expect(screen.getByText('Toggle Chat')).toBeInTheDocument()
    expect(screen.getByText('Create New Task')).toBeInTheDocument()
    expect(screen.queryByText('Go to Home')).not.toBeInTheDocument()
  })

  it('first item is selected by default', () => {
    renderPalette()
    // The first command item should have the selected styling (bg-blue-500/20)
    const firstItem = screen.getByTestId('command-item-nav-home')
    expect(firstItem.className).toContain('bg-blue-500/20')
  })
})

describe('CommandPalette concept search', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useAppStore.setState({ chatOpen: false })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows search results for matching tasks', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [{ id: '→086', priority: 'P1', title: 'Add concept search' }],
      ideas: [],
      query: 'search',
    })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')

    // Use fireEvent instead of userEvent since we're using fake timers
    fireEvent.change(input, { target: { value: 'search' } })

    // Advance past the debounce timer
    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(screen.getByTestId('search-tasks-section')).toBeInTheDocument()
      expect(screen.getByText('Add concept search')).toBeInTheDocument()
    })
  })

  it('shows search results for matching ideas', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [],
      ideas: [{ straw: 'add a search bar', timestamp: '2026-04-04T20:00:00Z', converted: false }],
      query: 'search',
    })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'search' } })

    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(screen.getByTestId('search-ideas-section')).toBeInTheDocument()
      expect(screen.getByText('add a search bar')).toBeInTheDocument()
    })
  })

  it('marks converted ideas with a label', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [],
      ideas: [{ straw: 'search feature idea', timestamp: '2026-04-04T20:00:00Z', converted: true }],
      query: 'search',
    })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'search' } })

    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(screen.getByText('(turned into a task)')).toBeInTheDocument()
    })
  })

  it('shows "no results" message when search returns empty', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [],
      ideas: [],
      query: 'xyznonexistent',
    })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'xyznonexistent' } })

    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(screen.getByTestId('search-no-results')).toBeInTheDocument()
      expect(screen.getByText('No matching tasks or ideas found.')).toBeInTheDocument()
    })
  })

  it('does not search when query is shorter than 2 characters', async () => {
    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'x' } })

    vi.advanceTimersByTime(400)

    // Should not have called the API
    expect(mockedApiGet).not.toHaveBeenCalledWith(expect.stringContaining('/search'))
  })

  it('calls the search API with the encoded query', async () => {
    mockedApiGet.mockResolvedValue({ tasks: [], ideas: [], query: 'my topic' })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'my topic' } })

    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/search?q=my%20topic')
    })
  })

  it('displays priority badges for tasks', async () => {
    mockedApiGet.mockResolvedValue({
      tasks: [{ id: '→001', priority: 'P0', title: 'Critical task' }],
      ideas: [],
      query: 'critical',
    })

    renderPalette()
    const input = screen.getByTestId('command-palette-input')
    fireEvent.change(input, { target: { value: 'critical' } })

    vi.advanceTimersByTime(400)

    await waitFor(() => {
      expect(screen.getByText('P0')).toBeInTheDocument()
    })
  })
})
