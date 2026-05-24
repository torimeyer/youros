import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { UniversalSearch } from './UniversalSearch'
import { useAppStore } from '../stores/app'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

import { api } from '../lib/api'
const mockedApiGet = vi.mocked(api.get)

function renderSearch(open = true, onClose = vi.fn()) {
  return {
    onClose,
    ...render(
      <MemoryRouter>
        <UniversalSearch open={open} onClose={onClose} />
      </MemoryRouter>
    ),
  }
}

describe('UniversalSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false })
    mockedApiGet.mockResolvedValue({ tasks: [], query: '' })
  })

  it('renders nothing when open is false', () => {
    renderSearch(false)
    expect(screen.queryByTestId('universal-search')).not.toBeInTheDocument()
  })

  it('renders a search input when open is true', () => {
    renderSearch(true)
    expect(screen.getByTestId('universal-search')).toBeInTheDocument()
    expect(screen.getByTestId('universal-search-input')).toBeInTheDocument()
  })

  it('calls onClose when Escape is pressed', () => {
    const onClose = vi.fn()
    renderSearch(true, onClose)
    const input = screen.getByTestId('universal-search-input')
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when clicking the backdrop', () => {
    const onClose = vi.fn()
    renderSearch(true, onClose)
    fireEvent.click(screen.getByTestId('universal-search-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not call onClose when clicking inside the modal', () => {
    const onClose = vi.fn()
    renderSearch(true, onClose)
    fireEvent.click(screen.getByTestId('universal-search'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('renders Navigate and Actions section headers', () => {
    renderSearch()
    expect(screen.getByText('Navigate')).toBeInTheDocument()
    expect(screen.getByText('Actions')).toBeInTheDocument()
  })

  it('renders all nav items', () => {
    renderSearch()
    expect(screen.getByTestId('nav-item-home')).toBeInTheDocument()
    expect(screen.getByTestId('nav-item-tasks')).toBeInTheDocument()
    expect(screen.getByTestId('nav-item-settings')).toBeInTheDocument()
    expect(screen.getByTestId('nav-item-gmail')).toBeInTheDocument()
    expect(screen.getByTestId('nav-item-confluence')).toBeInTheDocument()
  })

  it('renders action items with shortcuts', () => {
    renderSearch()
    expect(screen.getByTestId('action-item-toggle-chat')).toBeInTheDocument()
    expect(screen.getByTestId('action-item-new-task')).toBeInTheDocument()
  })

  it('navigates on nav item click', () => {
    const onClose = vi.fn()
    renderSearch(true, onClose)
    fireEvent.click(screen.getByTestId('nav-item-agents'))
    expect(mockNavigate).toHaveBeenCalledWith('/agents')
    expect(onClose).toHaveBeenCalled()
  })

  it('ArrowDown moves selection and Enter activates it', () => {
    const onClose = vi.fn()
    renderSearch(true, onClose)
    const input = screen.getByTestId('universal-search-input')
    // selectedIndex starts at 0 (Home). ArrowDown → index 1 (Tasks).
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockNavigate).toHaveBeenCalledWith('/tasks')
  })

  it('ArrowUp wraps from first to last item', () => {
    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    // 23 nav + 2 actions = 25 total. ArrowUp from 0 → 24 (last action = new-task).
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockNavigate).toHaveBeenCalledWith('/tasks?new=1')
  })

  it('first item is highlighted by default', () => {
    renderSearch()
    const first = screen.getByTestId('nav-item-home')
    expect(first.className).toContain('bg-blue-500/20')
  })
})

describe('UniversalSearch search', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useAppStore.setState({ chatOpen: false })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('fires all 3 endpoints when typing', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if ((url as string).includes('/search/deep')) return Promise.resolve([])
      if ((url as string).includes('/search/recall')) return Promise.resolve([])
      return Promise.resolve({ tasks: [], query: '' })
    })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'hello' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/search?q=hello')
      expect(mockedApiGet).toHaveBeenCalledWith('/search/deep?q=hello')
      expect(mockedApiGet).toHaveBeenCalledWith('/search/recall?q=hello')
    })
    expect(mockedApiGet).toHaveBeenCalledTimes(3)
  })

  it('shows task results with priority badge', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if ((url as string).includes('/search/deep')) return Promise.resolve([])
      if ((url as string).includes('/search/recall')) return Promise.resolve([])
      return Promise.resolve({
        tasks: [{ id: '→042', priority: 'P1', title: 'Fix the search box' }],
        query: 'search',
      })
    })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'search' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(screen.getByTestId('section-tasks')).toBeInTheDocument()
      expect(screen.getByTestId('task-item-→042').textContent).toContain('Fix the search box')
      expect(screen.getByText('P1')).toBeInTheDocument()
    })
  })

  it('does not call API when query is empty', async () => {
    renderSearch()
    // No typing → no API call
    vi.advanceTimersByTime(200)
    expect(mockedApiGet).not.toHaveBeenCalled()
  })

  it('hides task section when 0 results and query is non-empty', async () => {
    mockedApiGet.mockResolvedValue({ tasks: [], query: 'xyz' })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'xyz' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('section-tasks')).not.toBeInTheDocument()
  })

  it('shows deep results when /search/deep returns items', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if ((url as string).includes('/search/deep')) {
        return Promise.resolve([{ title: 'Needle about auth' }])
      }
      if ((url as string).includes('/search/recall')) return Promise.resolve([])
      return Promise.resolve({ tasks: [], query: '' })
    })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'auth' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(screen.getByTestId('section-deep')).toBeInTheDocument()
      expect(screen.getByTestId('deep-item-0').textContent).toContain('Needle about auth')
    })
  })

  it('shows transcript results when /search/recall returns items', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if ((url as string).includes('/search/recall')) {
        return Promise.resolve([{ title: 'Transcript: deploy convo' }])
      }
      if ((url as string).includes('/search/deep')) return Promise.resolve([])
      return Promise.resolve({ tasks: [], query: '' })
    })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'deploy' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(screen.getByTestId('section-transcripts')).toBeInTheDocument()
      expect(screen.getByTestId('transcript-item-0').textContent).toContain('Transcript: deploy convo')
    })
  })

  it('one failed endpoint does not kill the others', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if ((url as string).includes('/search/deep')) return Promise.reject(new Error('pitchfork offline'))
      if ((url as string).includes('/search/recall')) return Promise.resolve([])
      return Promise.resolve({
        tasks: [{ id: '→001', priority: 'P0', title: 'Still visible' }],
        query: 'test',
      })
    })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'test' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(screen.getByTestId('section-tasks')).toBeInTheDocument()
      expect(screen.getByText('Still visible')).toBeInTheDocument()
      expect(screen.queryByTestId('section-deep')).not.toBeInTheDocument()
    })
  })

  it('encodes special characters in query', async () => {
    mockedApiGet.mockResolvedValue({ tasks: [], query: '' })

    renderSearch()
    const input = screen.getByTestId('universal-search-input')
    fireEvent.change(input, { target: { value: 'auth flow' } })
    vi.advanceTimersByTime(200)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/search?q=auth%20flow')
    })
  })
})
