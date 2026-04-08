import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Ideas from './Ideas'

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
const mockedApiDelete = vi.mocked(api.delete)

const mockIdeasResponse = {
  clusters: [
    { name: 'Design', count: 2, items: ['Redesign sidebar', 'New color palette'] },
    { name: 'Performance', count: 1, items: ['Cache API responses'] },
  ],
  unclustered: ['Random thought about cats', 'Learn Rust'],
}

function renderIdeas() {
  return render(
    <MemoryRouter>
      <Ideas />
    </MemoryRouter>
  )
}

describe('Ideas page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockResolvedValue(mockIdeasResponse)
    mockedApiPost.mockResolvedValue({})
    mockedApiDelete.mockResolvedValue({})
  })

  it('renders ideas from API data', async () => {
    renderIdeas()

    await waitFor(() => {
      // Cluster items appear in both the compilations summary and in the ideas list,
      // so use getAllByText for those
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    expect(screen.getAllByText('New color palette').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Cache API responses').length).toBeGreaterThanOrEqual(1)
    // Unclustered items only appear in the ideas list
    expect(screen.getByText('Random thought about cats')).toBeInTheDocument()
    expect(screen.getByText('Learn Rust')).toBeInTheDocument()
  })

  it('calls api.get with /ideas on mount', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/ideas')
    })
  })

  it('count badge shows correct total number of ideas', async () => {
    renderIdeas()

    // Total: 3 from clusters + 2 unclustered = 5
    // The count appears in both the header badge and the Active tab button,
    // so target the specific badge element by its class.
    await waitFor(() => {
      const badge = document.querySelector('.bg-pink-500.text-white.text-xs.rounded-full')
      expect(badge).not.toBeNull()
      expect(badge!.textContent).toBe('5')
    })
  })

  it('shows the page title', async () => {
    renderIdeas()

    // The h1 "Ideas" heading
    const heading = screen.getByRole('heading', { name: 'Ideas' })
    expect(heading).toBeInTheDocument()
  })

  it('send button calls POST /api/ideas with input text', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText("Quick dump an idea (or just mention it in chat)")
    fireEvent.change(input, { target: { value: 'Build a rocket ship' } })

    const sendButton = screen.getByRole('button', { name: 'Send' })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas', { thought: 'Build a rocket ship' })
    })
  })

  it('send clears input after successful submission', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText("Quick dump an idea (or just mention it in chat)") as HTMLInputElement
    fireEvent.change(input, { target: { value: 'My idea' } })
    expect(input.value).toBe('My idea')

    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('send refetches data after submission', async () => {
    renderIdeas()

    // On mount, fetchData() calls both fetchActive() and fetchConverted(),
    // so api.get is called 2 times initially.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledTimes(2)
    })

    const input = screen.getByPlaceholderText("Quick dump an idea (or just mention it in chat)")
    fireEvent.change(input, { target: { value: 'Refetch test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    // After send, handleSend calls fetchActive() which adds 1 more call.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledTimes(3)
    })
  })

  it('send does not call API with empty input', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('send does not call API with whitespace-only input', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText("Quick dump an idea (or just mention it in chat)")
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('Enter key submits the input', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText("Quick dump an idea (or just mention it in chat)")
    fireEvent.change(input, { target: { value: 'Enter idea' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas', { thought: 'Enter idea' })
    })
  })

  it('compile button calls POST /api/ideas/compile', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    // The "Create Tasks" button in the suggested compilations section
    const createTasksButton = screen.getByRole('button', { name: 'Create Tasks' })
    fireEvent.click(createTasksButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas/compile')
    })
  })

  it('compile shows success message after completion', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Create Tasks' }))

    await waitFor(() => {
      expect(screen.getByText('Ideas compiled into tasks!')).toBeInTheDocument()
    })
  })

  it('compile shows error message on failure', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Server error'))

    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Create Tasks' }))

    await waitFor(() => {
      expect(screen.getByText('Compile failed. Try again.')).toBeInTheDocument()
    })
  })

  it('per-idea "Break into tasks" buttons call convert endpoint', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    // Each idea card has a "Break into tasks" button that calls handleConvert
    const breakButtons = screen.getAllByRole('button', { name: 'Break into tasks' })
    expect(breakButtons.length).toBeGreaterThan(0)

    fireEvent.click(breakButtons[0])

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas/convert', { straw: 'Redesign sidebar' })
    })
  })

  it('shows "Created N tasks" message when convert returns a task list', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    mockedApiPost.mockResolvedValueOnce({
      status: 'created',
      tasks: [
        { id: '1', title: 'A', description: '', priority: 'P2', order: 0 },
        { id: '2', title: 'B', description: '', priority: 'P2', order: 1 },
        { id: '3', title: 'C', description: '', priority: 'P2', order: 2 },
      ],
    })

    const breakButtons = screen.getAllByRole('button', { name: 'Break into tasks' })
    fireEvent.click(breakButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Created 3 tasks.')).toBeInTheDocument()
    })
  })

  it('shows clarification prompt when convert returns needs_clarification', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    mockedApiPost.mockResolvedValueOnce({
      status: 'needs_clarification',
      question: 'Web or mobile?',
      straw: 'Redesign sidebar',
    })

    const breakButtons = screen.getAllByRole('button', { name: 'Break into tasks' })
    fireEvent.click(breakButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Web or mobile?')).toBeInTheDocument()
    })
    // The quick question header appears.
    expect(screen.getByText('Quick question')).toBeInTheDocument()
  })

  it('renders suggested compilations section when clusters exist', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Suggested Compilations')).toBeInTheDocument()
    })
  })

  it('does not render suggested compilations when no clusters', async () => {
    mockedApiGet.mockResolvedValue({ clusters: [], unclustered: ['Just a thought'] })

    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Just a thought')).toBeInTheDocument()
    })

    expect(screen.queryByText('Suggested Compilations')).not.toBeInTheDocument()
  })

  it('handles API error on initial load gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))

    renderIdeas()

    // Should render without crashing, with empty state
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/ideas')
    })

    // Page title should still be visible
    expect(screen.getByRole('heading', { name: 'Ideas' })).toBeInTheDocument()
    // Count should be 0
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('sort toggle button switches between newest and oldest', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Newest First')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Newest First'))
    expect(screen.getByText('Oldest First')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Oldest First'))
    expect(screen.getByText('Newest First')).toBeInTheDocument()
  })

  it('each idea card has a remove button', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    const removeButtons = screen.getAllByTitle('Remove idea')
    expect(removeButtons.length).toBe(5) // 3 cluster + 2 unclustered
  })

  it('remove button calls DELETE /ideas/{straw}', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Learn Rust')).toBeInTheDocument()
    })

    const removeButtons = screen.getAllByTitle('Remove idea')
    // Click the last remove button (corresponds to "Learn Rust")
    fireEvent.click(removeButtons[removeButtons.length - 1])

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/ideas/Learn%20Rust')
    })
  })

  it('remove button shows success message after deletion', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    const removeButtons = screen.getAllByTitle('Remove idea')
    fireEvent.click(removeButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Idea removed.')).toBeInTheDocument()
    })
  })

  it('remove button shows error message on failure', async () => {
    mockedApiDelete.mockRejectedValueOnce(new Error('Server error'))

    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    const removeButtons = screen.getAllByTitle('Remove idea')
    fireEvent.click(removeButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Could not remove idea. Try again.')).toBeInTheDocument()
    })
  })
})
