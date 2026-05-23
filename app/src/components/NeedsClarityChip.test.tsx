import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { NeedsClarityChip, ReadinessCheck } from './NeedsClarityChip'

// ---------------------------------------------------------------------------
// Mock the api module
// ---------------------------------------------------------------------------

const mockApiPost = vi.fn()
const mockApiPatch = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    patch: (...args: unknown[]) => mockApiPatch(...args),
  },
}))

// ---------------------------------------------------------------------------
// Test data helpers
// ---------------------------------------------------------------------------

function makeCheck(name: string, passed: boolean, detail = 'some detail'): ReadinessCheck {
  return { name, passed, detail }
}

const failingChecks: ReadinessCheck[] = [
  makeCheck('has_ac_checkboxes', false, 'no checklist items found'),
  makeCheck('referenced_files_exist', false, '1 of 3 files found; missing: foo.ts'),
  makeCheck('in_repo_scope', false, 'description refers to work outside this project: upstream ostk'),
]

const oneFailingCheck: ReadinessCheck[] = [
  makeCheck('has_ac_checkboxes', false, 'no checklist items found'),
  makeCheck('in_repo_scope', true, 'task is scoped to this project'),
]

const allPassingChecks: ReadinessCheck[] = [
  makeCheck('has_ac_checkboxes', true, '3 items to check off'),
  makeCheck('in_repo_scope', true, 'task is scoped to this project'),
]

// ---------------------------------------------------------------------------
// Chip wording and stage=ready behavior
// ---------------------------------------------------------------------------

describe('Chip wording and stage', () => {
  it('shows "Add detail?" not "Needs clarity"', () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    const chip = screen.getByTestId('needs-clarity-chip')
    expect(chip).toHaveTextContent('Add detail?')
    expect(screen.queryByText('Needs clarity')).not.toBeInTheDocument()
  })

  it('when stage=ready shows icon-only chip with no "Add detail?" text', () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" stage="ready" />)
    const chip = screen.getByTestId('needs-clarity-chip')
    expect(chip).toBeInTheDocument()
    expect(screen.queryByText('Add detail?')).not.toBeInTheDocument()
  })

  it('when stage=ready chip is still clickable and opens the modal', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" stage="ready" />)
    const chip = screen.getByTestId('needs-clarity-chip')
    fireEvent.click(chip)
    expect(screen.getByTestId('needs-clarity-modal')).toBeInTheDocument()
  })

  it('when no stage (default) chip shows "Add detail?" text', () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    expect(screen.getByTestId('needs-clarity-chip')).toHaveTextContent('Add detail?')
  })
})

// ---------------------------------------------------------------------------
// Plain language label assertions
// ---------------------------------------------------------------------------

describe('CHECK_LABEL plain language', () => {
  it('uses "Work lives in this project" not "In-repo scope"', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.queryByText('In-repo scope')).not.toBeInTheDocument()
    expect(screen.getByText('Work lives in this project')).toBeInTheDocument()
  })

  it('uses "All mentioned files can be found" not "Referenced files exist"', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.queryByText('Referenced files exist')).not.toBeInTheDocument()
    expect(screen.getByText('All mentioned files can be found')).toBeInTheDocument()
  })

  it('uses "Steps to verify it\'s done" not "Acceptance criteria"', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.queryByText('Acceptance criteria')).not.toBeInTheDocument()
    expect(screen.getByText("Steps to verify it's done")).toBeInTheDocument()
  })

  it('uses "project" language in detail text', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    // The in_repo_scope detail now says "outside this project" (may appear in tooltip + detail p)
    const matches = screen.getAllByText(/outside this project/)
    expect(matches.length).toBeGreaterThan(0)
    expect(screen.queryByText(/out-of-repo/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// CHECK_PLACEHOLDER plain language
// ---------------------------------------------------------------------------

describe('CHECK_PLACEHOLDER plain language', () => {
  it('has_ac_checkboxes placeholder uses "steps" not "acceptance criteria"', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes')
    expect(textarea.getAttribute('placeholder')).toMatch(/steps/)
    expect(textarea.getAttribute('placeholder')).not.toMatch(/acceptance criteria/i)
  })

  it('in_repo_scope placeholder uses "project" not "repo"', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    const textarea = screen.getByTestId('clarity-input-in_repo_scope')
    expect(textarea.getAttribute('placeholder')).toMatch(/project/)
    expect(textarea.getAttribute('placeholder')).not.toMatch(/\brepo\b/)
  })
})

// ---------------------------------------------------------------------------
// FillAllGaps button visibility
// ---------------------------------------------------------------------------

describe('Fill all gaps button', () => {
  beforeEach(() => {
    mockApiPost.mockReset()
    mockApiPatch.mockReset()
  })

  it('renders when there are >=2 failing checks and a specPath', async () => {
    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('fill-all-gaps-btn')).toBeInTheDocument()
  })

  it('does NOT render when there is only 1 failing check', async () => {
    render(<NeedsClarityChip checks={oneFailingCheck} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.queryByTestId('fill-all-gaps-btn')).not.toBeInTheDocument()
  })

  it('does NOT render when all checks pass', async () => {
    render(<NeedsClarityChip checks={allPassingChecks} specPath="docs/spec/foo.md" />)
    // All passing → shows "Ready" chip, no modal
    expect(screen.queryByTestId('fill-all-gaps-btn')).not.toBeInTheDocument()
  })

  it('does NOT render when no specPath or taskId provided', async () => {
    render(<NeedsClarityChip checks={failingChecks} />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.queryByTestId('fill-all-gaps-btn')).not.toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // FillAllGaps button behavior
  // ---------------------------------------------------------------------------

  it('calls /clarity/suggest for each failing check in parallel on click', async () => {
    mockApiPost.mockResolvedValue({ proposed_fix: 'AI fix', rationale: 'reason' })

    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    await act(async () => {
      fireEvent.click(screen.getByTestId('fill-all-gaps-btn'))
    })

    await waitFor(() => {
      // 3 failing checks → 3 calls
      expect(mockApiPost).toHaveBeenCalledTimes(3)
    })

    // Each call targets the right endpoint with the right check name
    const callArgs = mockApiPost.mock.calls.map((c) => c[0] as string)
    expect(callArgs).toContain('/specs/docs/spec/foo.md/clarity/suggest')
  })

  it('populates each textarea after fill-all', async () => {
    mockApiPost.mockResolvedValue({ proposed_fix: 'AI fix here', rationale: 'reason' })

    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    await act(async () => {
      fireEvent.click(screen.getByTestId('fill-all-gaps-btn'))
    })

    await waitFor(() => {
      const firstTextarea = screen.getByTestId('clarity-input-has_ac_checkboxes') as HTMLTextAreaElement
      expect(firstTextarea.value).toBe('AI fix here')
    })
  })

  it('shows apply-all confirm buttons after fill completes', async () => {
    mockApiPost.mockResolvedValue({ proposed_fix: 'fix', rationale: 'r' })

    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    await act(async () => {
      fireEvent.click(screen.getByTestId('fill-all-gaps-btn'))
    })

    await waitFor(() => {
      expect(screen.getByTestId('apply-all-btn')).toBeInTheDocument()
      expect(screen.getByTestId('review-first-btn')).toBeInTheDocument()
    })
  })

  it('surfaces per-check errors inline when suggest fails', async () => {
    mockApiPost.mockRejectedValue(new Error('API unavailable'))

    render(<NeedsClarityChip checks={failingChecks} specPath="docs/spec/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    await act(async () => {
      fireEvent.click(screen.getByTestId('fill-all-gaps-btn'))
    })

    await waitFor(() => {
      expect(screen.getByTestId('clarity-suggest-error-has_ac_checkboxes')).toBeInTheDocument()
    })
  })

  it('also works for task mode with taskId', async () => {
    mockApiPost.mockResolvedValue({ proposed_fix: 'task fix', rationale: 'r' })

    const taskChecks: ReadinessCheck[] = [
      makeCheck('outcome_concrete', false, 'vague language'),
      makeCheck('in_repo_scope', false, 'description refers to work outside this project: upstream ostk'),
    ]

    render(
      <NeedsClarityChip
        checks={taskChecks}
        taskId="task-123"
        mode="task"
      />
    )
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('fill-all-gaps-btn')).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByTestId('fill-all-gaps-btn'))
    })

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        '/tasks/task-123/clarify/suggest',
        expect.objectContaining({ check: expect.any(String) })
      )
    })
  })
})
