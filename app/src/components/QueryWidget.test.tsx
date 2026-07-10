import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import QueryWidget, { formatDueLabel } from './QueryWidget'
import { QUERY_PRESETS } from './queryPresets'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
    },
  }
})

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
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

const jiraPreset = QUERY_PRESETS.find((p) => p.id === 'jira_due_soon')!
const confPreset = QUERY_PRESETS.find((p) => p.id === 'conf_edited_by_me')!

function renderWidget(preset = jiraPreset) {
  return render(
    <MemoryRouter>
      <QueryWidget preset={preset} />
    </MemoryRouter>
  )
}

describe('QueryWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders jira rows with status chip and due label', async () => {
    mockedGet.mockResolvedValue({
      rows: [
        {
          key: 'PROJ-1',
          summary: 'Fix the login page',
          status: 'In Progress',
          priority: 'High',
          type: 'Bug',
          updated: '2026-07-09T12:00:00Z',
          due: '2030-01-01',
          url: 'https://example.com',
        },
      ],
    })

    renderWidget()

    await waitFor(() => {
      expect(screen.getByText('Fix the login page')).toBeInTheDocument()
    })
    expect(screen.getByText('PROJ-1')).toBeInTheDocument()
    expect(screen.getByText('In Progress')).toBeInTheDocument()
    expect(screen.getByText(/^due in \d+ days$/)).toBeInTheDocument()
  })

  it('renders confluence rows', async () => {
    mockedGet.mockResolvedValue({
      rows: [
        {
          id: '42',
          title: 'Architecture Overview',
          type: 'page',
          updated: '2026-07-09T12:00:00Z',
          url: 'https://example.com',
        },
      ],
    })

    renderWidget(confPreset)

    await waitFor(() => {
      expect(screen.getByText('Architecture Overview')).toBeInTheDocument()
    })
  })

  it('shows preset empty text when no rows returned', async () => {
    mockedGet.mockResolvedValue({ rows: [] })

    renderWidget()

    await waitFor(() => {
      expect(screen.getByText(jiraPreset.emptyText)).toBeInTheDocument()
    })
  })

  it('shows error state on fetch failure', async () => {
    mockedGet.mockRejectedValue(new Error('network error'))

    renderWidget()

    await waitFor(() => {
      expect(screen.getByText('Not connected or failed to load')).toBeInTheDocument()
    })
  })

  it('navigates to jira issue on row click', async () => {
    mockedGet.mockResolvedValue({
      rows: [
        {
          key: 'PROJ-2',
          summary: 'Another issue',
          status: 'Open',
          priority: 'Low',
          type: 'Task',
          updated: '2026-07-09T12:00:00Z',
          due: null,
          url: 'https://example.com',
        },
      ],
    })

    renderWidget()

    await waitFor(() => {
      expect(screen.getByText('Another issue')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Another issue'))
    expect(mockNavigate).toHaveBeenCalledWith('/jira/PROJ-2')
  })

  it('navigates to confluence page on row click', async () => {
    mockedGet.mockResolvedValue({
      rows: [
        {
          id: '99',
          title: 'My Page',
          type: 'page',
          updated: '2026-07-09T12:00:00Z',
          url: 'https://example.com',
        },
      ],
    })

    renderWidget(confPreset)

    await waitFor(() => {
      expect(screen.getByText('My Page')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('My Page'))
    expect(mockNavigate).toHaveBeenCalledWith('/confluence/99')
  })
})

describe('formatDueLabel', () => {
  const now = new Date('2026-07-10T12:00:00Z')

  it('returns "due today" for same day', () => {
    expect(formatDueLabel('2026-07-10', now)).toBe('due today')
  })

  it('returns "due tomorrow" for next day', () => {
    expect(formatDueLabel('2026-07-11', now)).toBe('due tomorrow')
  })

  it('returns "due in N days" for future dates', () => {
    expect(formatDueLabel('2026-07-15', now)).toBe('due in 5 days')
  })

  it('returns "overdue N days" for past dates', () => {
    expect(formatDueLabel('2026-07-08', now)).toBe('overdue 2 days')
  })

  it('returns "overdue 1 day" for yesterday', () => {
    expect(formatDueLabel('2026-07-09', now)).toBe('overdue 1 day')
  })
})
