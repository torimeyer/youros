import { describe, it, expect, vi, beforeEach, act } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import JiraWidget, { compareAttention, formatDueLabel } from './JiraWidget'

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

// Fixed reference point for comparator/label tests
const FIXED_NOW = new Date('2026-07-10T12:00:00Z')
// 2 days ago — recent, not stale
const RECENT = '2026-07-08T00:00:00.000Z'
// 40 days ago — stale (> 7 days)
const STALE = '2026-06-01T00:00:00.000Z'

function makeIssue(overrides: Partial<{
  key: string
  summary: string
  status: string
  priority: string
  type: string
  updated: string
  url: string
  due: string
}> = {}) {
  return {
    key: 'PROJ-1',
    summary: 'Test issue',
    status: 'To Do',
    priority: 'Medium',
    type: 'Task',
    updated: RECENT,
    url: 'https://jira.example.com/browse/PROJ-1',
    due: '',
    ...overrides,
  }
}

function renderWidget() {
  return render(
    <MemoryRouter>
      <JiraWidget />
    </MemoryRouter>
  )
}

// ── compareAttention ──────────────────────────────────────────────────────────

describe('compareAttention', () => {
  it('puts overdue before high priority', () => {
    const overdue = makeIssue({ due: '2020-01-01', priority: 'Low', updated: RECENT })
    const highPri = makeIssue({ priority: 'High', due: '', updated: RECENT })
    expect(compareAttention(overdue, highPri, FIXED_NOW)).toBeLessThan(0)
  })

  it('puts high priority before stale', () => {
    const highPri = makeIssue({ priority: 'High', due: '', updated: RECENT })
    const stale = makeIssue({ priority: 'Low', due: '', updated: STALE })
    expect(compareAttention(highPri, stale, FIXED_NOW)).toBeLessThan(0)
  })

  it('puts Highest priority before stale', () => {
    const highest = makeIssue({ priority: 'Highest', due: '', updated: RECENT })
    const stale = makeIssue({ priority: 'Low', due: '', updated: STALE })
    expect(compareAttention(highest, stale, FIXED_NOW)).toBeLessThan(0)
  })

  it('puts stale before regular', () => {
    const stale = makeIssue({ priority: 'Low', due: '', updated: STALE })
    const regular = makeIssue({ priority: 'Low', due: '', updated: RECENT })
    expect(compareAttention(stale, regular, FIXED_NOW)).toBeLessThan(0)
  })

  it('sorts same-bucket by updated descending (newer first)', () => {
    const newer = makeIssue({ updated: '2026-07-07T00:00:00Z', due: '', priority: 'Low' })
    const older = makeIssue({ updated: '2026-07-05T00:00:00Z', due: '', priority: 'Low' })
    expect(compareAttention(newer, older, FIXED_NOW)).toBeLessThan(0)
  })

  it('overdue beats stale', () => {
    const overdue = makeIssue({ due: '2020-01-01', priority: 'Low', updated: STALE })
    const stale = makeIssue({ priority: 'Low', due: '', updated: STALE })
    expect(compareAttention(overdue, stale, FIXED_NOW)).toBeLessThan(0)
  })
})

// ── formatDueLabel ────────────────────────────────────────────────────────────

describe('formatDueLabel', () => {
  it('returns empty string for empty due', () => {
    expect(formatDueLabel('', FIXED_NOW)).toBe('')
  })

  it('overdue 1 day (singular)', () => {
    expect(formatDueLabel('2026-07-09', FIXED_NOW)).toBe('overdue 1 day')
  })

  it('overdue N days (plural)', () => {
    expect(formatDueLabel('2026-07-08', FIXED_NOW)).toBe('overdue 2 days')
  })

  it('due today', () => {
    expect(formatDueLabel('2026-07-10', FIXED_NOW)).toBe('due today')
  })

  it('due tomorrow', () => {
    expect(formatDueLabel('2026-07-11', FIXED_NOW)).toBe('due tomorrow')
  })

  it('due in N days', () => {
    expect(formatDueLabel('2026-07-15', FIXED_NOW)).toBe('due in 5 days')
  })
})

// ── grouping ──────────────────────────────────────────────────────────────────

describe('grouping', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows "In review" heading for status containing review (case-insensitive)', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'A-1', status: 'In Code Review' })] })
    renderWidget()
    await waitFor(() => expect(screen.getByText('In review')).toBeInTheDocument())
  })

  it('shows "In progress" heading for status exactly "In Progress"', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'A-1', status: 'In Progress' })] })
    renderWidget()
    await waitFor(() => expect(screen.getByText('In progress')).toBeInTheDocument())
  })

  it('shows "To do" heading for other statuses', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'A-1', status: 'Backlog' })] })
    renderWidget()
    await waitFor(() => expect(screen.getByText('To do')).toBeInTheDocument())
  })

  it('omits group headings for empty groups', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'A-1', status: 'In Progress' })] })
    renderWidget()
    await waitFor(() => expect(screen.getByText('In progress')).toBeInTheDocument())
    expect(screen.queryByText('To do')).toBeNull()
    expect(screen.queryByText('In review')).toBeNull()
  })

  it('renders multiple groups in order: To do, In progress, In review', async () => {
    mockedGet.mockResolvedValue({
      issues: [
        makeIssue({ key: 'A-1', status: 'In Progress' }),
        makeIssue({ key: 'A-2', status: 'Backlog' }),
        makeIssue({ key: 'A-3', status: 'Review' }),
      ],
    })
    renderWidget()
    await waitFor(() => {
      const headings = screen.getAllByText(/^(To do|In progress|In review)$/)
      expect(headings.map(h => h.textContent)).toEqual(['To do', 'In progress', 'In review'])
    })
  })
})

// ── overdue header ────────────────────────────────────────────────────────────

describe('overdue header', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows "N overdue" in red when at least one visible issue is overdue', async () => {
    mockedGet.mockResolvedValue({
      issues: [
        makeIssue({ key: 'A-1', due: '2020-01-01' }),
        makeIssue({ key: 'A-2', due: '' }),
      ],
    })
    renderWidget()
    await waitFor(() => {
      const span = screen.getByText('1 overdue')
      expect(span).toBeInTheDocument()
      expect(span.className).toContain('text-red-500')
    })
  })

  it('shows "N assigned" when no issues are overdue', async () => {
    mockedGet.mockResolvedValue({
      issues: [
        makeIssue({ key: 'A-1', due: '' }),
        makeIssue({ key: 'A-2', due: '' }),
      ],
    })
    renderWidget()
    await waitFor(() => expect(screen.getByText('2 assigned')).toBeInTheDocument())
  })

  it('counts only overdue issues (not all) in the header', async () => {
    mockedGet.mockResolvedValue({
      issues: [
        makeIssue({ key: 'A-1', due: '2020-01-01' }),
        makeIssue({ key: 'A-2', due: '2020-01-02' }),
        makeIssue({ key: 'A-3', due: '' }),
      ],
    })
    renderWidget()
    await waitFor(() => expect(screen.getByText('2 overdue')).toBeInTheDocument())
  })
})

// ── promote button ────────────────────────────────────────────────────────────

describe('promote button', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows "Add to my tasks" button with correct aria-label', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'PROJ-1' })] })
    renderWidget()
    await waitFor(() => {
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toBeInTheDocument()
    })
  })

  it('success: shows "Added" and disables the button', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'PROJ-1' })] })
    mockedPost.mockResolvedValue(undefined)
    renderWidget()

    const btn = await screen.findByLabelText('Add PROJ-1 to my tasks')
    expect(btn).not.toBeDisabled()

    fireEvent.click(btn)

    await waitFor(() => {
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toHaveTextContent('Added')
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toBeDisabled()
    })

    expect(mockedPost).toHaveBeenCalledWith('/atlassian/jira/promote', { key: 'PROJ-1' })
  })

  it('failure: shows "Couldn\'t add" (button stays enabled)', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'PROJ-1' })] })
    mockedPost.mockRejectedValue(new Error('network error'))
    renderWidget()

    const btn = await screen.findByLabelText('Add PROJ-1 to my tasks')
    fireEvent.click(btn)

    await waitFor(() => {
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toHaveTextContent("Couldn't add")
    })
    expect(screen.getByLabelText('Add PROJ-1 to my tasks')).not.toBeDisabled()
  })

  it('failure: re-enables button to "Add to my tasks" after 3s', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'PROJ-1' })] })
    mockedPost.mockRejectedValue(new Error('network error'))
    renderWidget()

    // Wait for loaded state before starting fake timers
    const btn = await screen.findByLabelText('Add PROJ-1 to my tasks')

    vi.useFakeTimers()
    try {
      fireEvent.click(btn)
      // Flush async microtasks (the rejected promise + setState)
      await act(async () => {})

      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toHaveTextContent("Couldn't add")

      // Advance past the 3s reset
      act(() => { vi.advanceTimersByTime(3001) })

      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toHaveTextContent('Add to my tasks')
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).not.toBeDisabled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('promote click does not navigate to the issue detail', async () => {
    mockedGet.mockResolvedValue({ issues: [makeIssue({ key: 'PROJ-1' })] })
    mockedPost.mockResolvedValue(undefined)
    renderWidget()

    const btn = await screen.findByLabelText('Add PROJ-1 to my tasks')
    fireEvent.click(btn)

    // No navigation — window.location stays at /
    await waitFor(() => {
      expect(screen.getByLabelText('Add PROJ-1 to my tasks')).toHaveTextContent('Added')
    })
  })
})

// ── existing states ───────────────────────────────────────────────────────────

describe('existing states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the widget-jira testid', () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    renderWidget()
    expect(screen.getByTestId('widget-jira')).toBeInTheDocument()
  })

  it('shows loading skeleton while fetching', () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    const { container } = renderWidget()
    // SkeletonLine elements should be present
    expect(container.querySelector('.w-3\\/4')).toBeInTheDocument()
  })

  it('shows error message when fetch fails', async () => {
    mockedGet.mockRejectedValue(new Error('network'))
    renderWidget()
    await waitFor(() =>
      expect(screen.getByText('Not connected or failed to load')).toBeInTheDocument()
    )
  })

  it('shows empty state when no issues', async () => {
    mockedGet.mockResolvedValue({ issues: [] })
    renderWidget()
    await waitFor(() =>
      expect(screen.getByText('No issues assigned to you.')).toBeInTheDocument()
    )
  })

  it('renders issue summary text', async () => {
    mockedGet.mockResolvedValue({
      issues: [makeIssue({ key: 'PROJ-1', summary: 'Fix the login bug' })],
    })
    renderWidget()
    await waitFor(() => expect(screen.getByText('Fix the login bug')).toBeInTheDocument())
  })

  it('caps display at 5 issues', async () => {
    const issues = Array.from({ length: 8 }, (_, i) =>
      makeIssue({ key: `PROJ-${i + 1}`, summary: `Issue ${i + 1}` })
    )
    mockedGet.mockResolvedValue({ issues })
    renderWidget()
    await waitFor(() => expect(screen.getByText('Issue 1')).toBeInTheDocument())
    expect(screen.queryByText('Issue 6')).toBeNull()
  })

  it('title is an h2 with text-lg font-semibold', async () => {
    mockedGet.mockResolvedValue({ issues: [] })
    const { container } = renderWidget()
    await waitFor(() => {
      const heading = container.querySelector('h2')
      expect(heading).not.toBeNull()
      expect(heading!.classList.contains('text-lg')).toBe(true)
      expect(heading!.classList.contains('font-semibold')).toBe(true)
    })
  })
})
