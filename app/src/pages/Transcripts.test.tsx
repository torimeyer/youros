import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Transcripts from './Transcripts'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

const mockTranscriptsResponse = {
  transcripts: [
    {
      session_id: 'sess-001',
      name: 'Build the dashboard',
      project: 'torios',
      cwd: '/home/user/torios',
      started_at: '2026-04-04 10:00 AM',
      kind: 'interactive',
      entrypoint: 'claude',
      first_message: 'Let me build a dashboard',
      message_counts: { user: 5, assistant: 5, tool_calls: 3 },
      file_size_bytes: 2048,
    },
    {
      session_id: 'sess-002',
      name: 'Fix login bug',
      project: 'torios',
      cwd: '/home/user/torios',
      started_at: '2026-04-03 2:00 PM',
      kind: 'task',
      entrypoint: 'agent',
      first_message: 'Fix the login flow',
      message_counts: { user: 2, assistant: 8, tool_calls: 12 },
      file_size_bytes: 8192,
    },
  ],
  total: 2,
}

const mockTranscriptDetail = {
  session_id: 'sess-001',
  name: 'Build the dashboard',
  cwd: '/home/user/torios',
  started_at: '2026-04-04 10:00 AM',
  kind: 'interactive',
  total_messages: 10,
  messages: [
    {
      role: 'user' as const,
      text: 'Build me a dashboard',
      timestamp: '10:00 AM',
    },
    {
      role: 'assistant' as const,
      text: 'I will create a dashboard for you.',
      tool_uses: ['read_file', 'write_file'],
      timestamp: '10:01 AM',
    },
  ],
}

function renderTranscripts() {
  return render(
    <MemoryRouter>
      <Transcripts />
    </MemoryRouter>
  )
}

describe('Transcripts page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
    mockedApiGet.mockResolvedValue(mockTranscriptsResponse)
  })

  // --- List view ---

  it('renders the page title', async () => {
    renderTranscripts()
    expect(screen.getByRole('heading', { name: 'Transcripts' })).toBeInTheDocument()
  })

  it('renders subtitle text', async () => {
    renderTranscripts()
    expect(
      screen.getByText('Conversation history from your AI sessions')
    ).toBeInTheDocument()
  })

  it('fetches transcripts on mount', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/transcripts')
    })
  })

  it('renders transcript list from API data', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })
    expect(screen.getByText('Fix login bug')).toBeInTheDocument()
  })

  it('shows session count', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('2 sessions')).toBeInTheDocument()
    })
  })

  it('shows loading state before data arrives', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderTranscripts()

    expect(screen.getByText('Loading transcripts...')).toBeInTheDocument()
  })

  it('shows empty state when no transcripts', async () => {
    mockedApiGet.mockResolvedValue({ transcripts: [], total: 0 })
    renderTranscripts()

    await waitFor(() => {
      expect(
        screen.getByText(
          'No transcripts found. Conversations you have with AI will appear here.'
        )
      ).toBeInTheDocument()
    })
  })

  it('shows error state on API failure', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Could not load transcripts.')).toBeInTheDocument()
    })
  })

  it('shows first message preview for transcripts', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Let me build a dashboard')).toBeInTheDocument()
    })
  })

  it('shows message count for each transcript', async () => {
    renderTranscripts()

    await waitFor(() => {
      // Both transcripts have 10 messages (5+5 and 2+8)
      const messageLabels = screen.getAllByText('10 messages')
      expect(messageLabels.length).toBe(2)
    })
  })

  it('shows tool call count when present', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('3 tool calls')).toBeInTheDocument()
    })
    expect(screen.getByText('12 tool calls')).toBeInTheDocument()
  })

  it('shows kind badges for transcripts', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('conversation')).toBeInTheDocument()
    })
    expect(screen.getByText('agent')).toBeInTheDocument()
  })

  // --- Search ---

  it('has a search input', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search messages...')).toBeInTheDocument()
    })
  })

  it('has a Search button', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Search' })).toBeInTheDocument()
    })
  })

  it('clicking Search triggers a search API call', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Search messages...')
    fireEvent.change(input, { target: { value: 'dashboard' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/transcripts?search=dashboard')
    })
  })

  it('pressing Enter in search input triggers search', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Search messages...')
    fireEvent.change(input, { target: { value: 'login' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/transcripts?search=login')
    })
  })

  it('shows result count when search is active', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.includes('search='))
        return { transcripts: [mockTranscriptsResponse.transcripts[0]], total: 1 }
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Search messages...')
    fireEvent.change(input, { target: { value: 'dashboard' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(screen.getByText('1 result found')).toBeInTheDocument()
    })
  })

  it('shows no results message when search returns nothing', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.includes('search=')) return { transcripts: [], total: 0 }
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Search messages...')
    fireEvent.change(input, { target: { value: 'nonexistent' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(
        screen.getByText(
          'No transcripts match your search. Try different terms or clear the filters.'
        )
      ).toBeInTheDocument()
    })
  })

  // --- Date range filter ---

  it('shows date range dropdown', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByDisplayValue('All time')).toBeInTheDocument()
    })
  })

  it('changing date range triggers a filtered API call', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const select = screen.getByDisplayValue('All time')
    fireEvent.change(select, { target: { value: 'today' } })

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/transcripts?date_range=today')
    })
  })

  // --- Clear filters ---

  it('shows Clear filters button when filters are active', async () => {
    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    const select = screen.getByDisplayValue('All time')
    fireEvent.change(select, { target: { value: 'today' } })

    await waitFor(() => {
      expect(screen.getByText('Clear filters')).toBeInTheDocument()
    })
  })

  // --- Detail view ---

  it('clicking a transcript opens the detail view', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return mockTranscriptDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/transcripts/sess-001?limit=200')
    })
  })

  it('detail view shows conversation messages', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return mockTranscriptDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Build me a dashboard')).toBeInTheDocument()
    })
    expect(
      screen.getByText('I will create a dashboard for you.')
    ).toBeInTheDocument()
  })

  it('detail view shows tool uses', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return mockTranscriptDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('read_file')).toBeInTheDocument()
    })
    expect(screen.getByText('write_file')).toBeInTheDocument()
  })

  it('detail view shows role labels', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return mockTranscriptDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('You')).toBeInTheDocument()
    })
    expect(screen.getByText('myOS')).toBeInTheDocument()
  })

  it('detail view has a back button that returns to list', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return mockTranscriptDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Build me a dashboard')).toBeInTheDocument()
    })

    // Click the back button (arrow_back icon)
    const backButtons = document.querySelectorAll('button')
    const backButton = Array.from(backButtons).find(
      (btn) => btn.textContent?.includes('arrow_back')
    )
    expect(backButton).toBeTruthy()
    fireEvent.click(backButton!)

    // Should return to the list view
    await waitFor(() => {
      expect(
        screen.getByText('Conversation history from your AI sessions')
      ).toBeInTheDocument()
    })
  })

  it('detail view shows message count when truncated', async () => {
    const largeDetail = {
      ...mockTranscriptDetail,
      total_messages: 500,
      messages: mockTranscriptDetail.messages,
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return largeDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Showing 2 of 500 messages.')).toBeInTheDocument()
    })
  })

  it('detail view shows empty message state', async () => {
    const emptyDetail = {
      ...mockTranscriptDetail,
      messages: [],
      total_messages: 0,
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path.startsWith('/transcripts/')) return emptyDetail
      return mockTranscriptsResponse
    })

    renderTranscripts()

    await waitFor(() => {
      expect(screen.getByText('Build the dashboard')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build the dashboard').closest('button')!)

    await waitFor(() => {
      expect(
        screen.getByText(
          'This transcript has no readable messages. It may contain only system or tool data.'
        )
      ).toBeInTheDocument()
    })
  })
})
