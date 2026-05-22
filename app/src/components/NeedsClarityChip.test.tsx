import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NeedsClarityChip, type ReadinessCheck } from './NeedsClarityChip'

vi.mock('../lib/api', () => ({
  api: {
    patch: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockedApiPatch = vi.mocked(api.patch)
const mockedApiPost = vi.mocked(api.post)

// Reduced spec checks (Clarity-1: 5 checks for specs)
const failedChecks: ReadinessCheck[] = [
  { name: 'has_ac_checkboxes', passed: false, detail: 'no checkboxes found' },
  { name: 'no_vague_ac', passed: true, detail: 'ok' },
  { name: 'has_file_paths', passed: false, detail: 'no file paths found' },
  { name: 'referenced_files_exist', passed: true, detail: 'ok' },
  { name: 'in_repo_scope', passed: true, detail: 'ok' },
]

const allPassedChecks: ReadinessCheck[] = failedChecks.map((c) => ({ ...c, passed: true, detail: 'ok' }))

// Task checks (Clarity-1: 3 checks for tasks)
const taskChecks: ReadinessCheck[] = [
  { name: 'outcome_concrete', passed: false, detail: 'title is vague' },
  { name: 'in_repo_scope', passed: true, detail: 'ok' },
  { name: 'is_unblocked', passed: true, detail: 'no blockers' },
]

const allPassedTaskChecks: ReadinessCheck[] = taskChecks.map((c) => ({ ...c, passed: true, detail: 'ok' }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('NeedsClarityChip', () => {
  it('renders amber chip with "Needs clarity" label', () => {
    render(<NeedsClarityChip checks={failedChecks} />)
    expect(screen.getByTestId('needs-clarity-chip')).toBeDefined()
    expect(screen.getByText(/needs clarity/i)).toBeDefined()
  })

  it('chip has amber/yellow styling, not purple', () => {
    render(<NeedsClarityChip checks={failedChecks} />)
    const chip = screen.getByTestId('needs-clarity-chip')
    const cls = chip.className
    expect(cls).toMatch(/amber|yellow/)
    expect(cls).not.toMatch(/violet/)
  })

  it('shows tooltip listing failed checks', () => {
    render(<NeedsClarityChip checks={failedChecks} />)
    const chip = screen.getByTestId('needs-clarity-chip')
    const title = chip.getAttribute('title') || ''
    expect(title).toContain('has_ac_checkboxes')
    expect(title).toContain('has_file_paths')
  })

  it('tooltip marks failed checks with ✗', () => {
    render(<NeedsClarityChip checks={failedChecks} />)
    const chip = screen.getByTestId('needs-clarity-chip')
    expect(chip.getAttribute('title')).toContain('✗')
  })

  it('renders nothing when no checks provided', () => {
    const { container } = render(<NeedsClarityChip checks={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('exports ReadinessCheck type', () => {
    const check: ReadinessCheck = { name: 'test', passed: true, detail: 'ok' }
    expect(check.name).toBe('test')
  })

  it('chip is a button (clickable)', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    const chip = screen.getByTestId('needs-clarity-chip')
    expect(chip.tagName).toBe('BUTTON')
  })

  it('needs clarity chip opens modal on click', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    expect(screen.queryByTestId('needs-clarity-modal')).toBeNull()
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('needs-clarity-modal')).toBeDefined()
  })

  it('modal lists failing checks with pass/fail icons', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByText(/has acceptance criteria/i)).toBeDefined()
    expect(screen.getByText(/references real files/i)).toBeDefined()
  })

  it('modal shows textarea for each failing check', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('clarity-input-has_ac_checkboxes')).toBeDefined()
  })

  it('modal shows AI suggest button for each failing check', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('clarity-suggest-has_ac_checkboxes')).toBeDefined()
  })

  it('Accept button replaces Save button label', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    const saveBtn = screen.getByTestId('clarity-save-has_ac_checkboxes')
    expect(saveBtn.textContent).toMatch(/accept/i)
    expect(saveBtn.textContent).not.toMatch(/^save$/i)
  })

  it('AI suggest button calls /clarity/suggest and fills textarea', async () => {
    mockedApiPost.mockResolvedValueOnce({
      proposed_fix: 'Add acceptance criteria here',
      rationale: 'The spec is missing checkboxes',
    })

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    fireEvent.click(screen.getByTestId('clarity-suggest-has_ac_checkboxes'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/specs/docs/draft/foo.md/clarity/suggest',
        { check: 'has_ac_checkboxes' }
      )
    })

    await waitFor(() => {
      const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes') as HTMLTextAreaElement
      expect(textarea.value).toBe('Add acceptance criteria here')
    })

    expect(screen.getByTestId('clarity-rationale-has_ac_checkboxes').textContent).toContain(
      'missing checkboxes'
    )
  })

  it('AI suggest button in task mode calls /clarify/suggest', async () => {
    mockedApiPost.mockResolvedValueOnce({
      proposed_fix: 'Make the title concrete',
      rationale: 'Title is too vague',
    })

    render(<NeedsClarityChip mode="task" checks={taskChecks} taskId="task-abc" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    fireEvent.click(screen.getByTestId('clarity-suggest-outcome_concrete'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/tasks/task-abc/clarify/suggest',
        { check: 'outcome_concrete' }
      )
    })
  })

  it('saving a clarity fix in spec mode calls PATCH endpoint and refreshes checks', async () => {
    const updatedChecks = failedChecks.map((c) =>
      c.name === 'has_ac_checkboxes' ? { ...c, passed: true, detail: 'fixed' } : c
    )
    mockedApiPatch.mockResolvedValueOnce({ checks: updatedChecks, ready: false })

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes')
    fireEvent.change(textarea, { target: { value: '- [ ] add login button\n- [ ] add logout button' } })

    const saveBtn = screen.getByTestId('clarity-save-has_ac_checkboxes')
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockedApiPatch).toHaveBeenCalledWith(
        '/specs/docs/draft/foo.md/clarity',
        { check: 'has_ac_checkboxes', fix: expect.stringContaining('login button') }
      )
    })
  })

  it('applying a clarity fix in task mode calls POST /clarify/apply', async () => {
    const updatedChecks = allPassedTaskChecks
    mockedApiPost.mockResolvedValueOnce({ checks: updatedChecks, ready: true })

    const onResolved = vi.fn()
    render(<NeedsClarityChip mode="task" checks={taskChecks} taskId="task-abc" onResolved={onResolved} />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    const textarea = screen.getByTestId('clarity-input-outcome_concrete')
    fireEvent.change(textarea, { target: { value: 'Fix the login button alignment on mobile' } })
    fireEvent.click(screen.getByTestId('clarity-save-outcome_concrete'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/tasks/task-abc/clarify/apply',
        { check: 'outcome_concrete', fix: 'Fix the login button alignment on mobile' }
      )
    })
    await waitFor(() => expect(onResolved).toHaveBeenCalled())
  })

  it('modal closes and onResolved fires when all checks pass after save', async () => {
    const onResolved = vi.fn()
    mockedApiPatch.mockResolvedValueOnce({ checks: allPassedChecks, ready: true })

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" onResolved={onResolved} />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes')
    fireEvent.change(textarea, { target: { value: '- [ ] do the thing' } })
    fireEvent.click(screen.getByTestId('clarity-save-has_ac_checkboxes'))

    await waitFor(() => {
      expect(onResolved).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('needs-clarity-modal')).toBeNull()
  })

  it('task mode filters hidden legacy checks from render', () => {
    const checksWithLegacy: ReadinessCheck[] = [
      { name: 'plan_path_present', passed: false, detail: 'missing' },
      { name: 'outcome_concrete', passed: false, detail: 'vague' },
    ]
    render(<NeedsClarityChip mode="task" checks={checksWithLegacy} taskId="task-xyz" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    // outcome_concrete should appear
    expect(screen.getByTestId('clarity-input-outcome_concrete')).toBeDefined()
    // plan_path_present should be filtered out
    expect(screen.queryByTestId('clarity-input-plan_path_present')).toBeNull()
  })

  // W1: AI suggest error display
  it('shows error message below AI suggest button when suggest fails', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Service unavailable'))

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    fireEvent.click(screen.getByTestId('clarity-suggest-has_ac_checkboxes'))

    await waitFor(() => {
      expect(screen.getByTestId('clarity-suggest-error-has_ac_checkboxes')).toBeDefined()
    })
    expect(screen.getByTestId('clarity-suggest-error-has_ac_checkboxes').textContent).toContain(
      'Service unavailable'
    )
  })

  it('shows Settings link when API key error occurs', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('No Anthropic API key configured. Add one in Settings to use AI suggestions.'))

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    fireEvent.click(screen.getByTestId('clarity-suggest-has_ac_checkboxes'))

    await waitFor(() => {
      expect(screen.getByTestId('clarity-suggest-error-has_ac_checkboxes')).toBeDefined()
    })
    const errorEl = screen.getByTestId('clarity-suggest-error-has_ac_checkboxes')
    const link = errorEl.querySelector('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toBe('/settings')
  })

  it('clears suggest error when user types in textarea', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Service unavailable'))

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    fireEvent.click(screen.getByTestId('clarity-suggest-has_ac_checkboxes'))

    await waitFor(() => {
      expect(screen.getByTestId('clarity-suggest-error-has_ac_checkboxes')).toBeDefined()
    })

    fireEvent.change(screen.getByTestId('clarity-input-has_ac_checkboxes'), {
      target: { value: '- [ ] some criteria' },
    })

    await waitFor(() => {
      expect(screen.queryByTestId('clarity-suggest-error-has_ac_checkboxes')).toBeNull()
    })
  })

  // W2: per-check placeholder hints
  it('textarea has check-specific placeholder for has_ac_checkboxes', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes') as HTMLTextAreaElement
    expect(textarea.placeholder).toContain('- [ ] When X, the result is Y')
  })

  it('textarea has check-specific placeholder for has_file_paths', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    const textarea = screen.getByTestId('clarity-input-has_file_paths') as HTMLTextAreaElement
    expect(textarea.placeholder).toContain('api/routers/foo.py')
  })

  // →1602: merged AC row
  it('collapses has_ac_checkboxes and no_vague_ac into one Acceptance criteria row', () => {
    const checks: ReadinessCheck[] = [
      { name: 'has_ac_checkboxes', passed: false, detail: "no '- [ ]' lines found" },
      { name: 'no_vague_ac', passed: false, detail: 'not evaluated — no AC' },
      { name: 'has_file_paths', passed: true, detail: 'ok' },
      { name: 'in_repo_scope', passed: true, detail: 'ok' },
    ]
    render(<NeedsClarityChip checks={checks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    // Only one row labeled "Acceptance criteria"
    expect(screen.getAllByText(/acceptance criteria/i)).toHaveLength(1)
    // The old separate label must not appear
    expect(screen.queryByText(/no vague acceptance criteria/i)).toBeNull()
    // Detail from the primary failing check
    expect(screen.getByText(/no '- \[ \]' lines found/i)).toBeDefined()
  })

  it('merged AC row shows FAIL with vague-token reason when AC exist but contain TBD', () => {
    const checks: ReadinessCheck[] = [
      { name: 'has_ac_checkboxes', passed: true, detail: '2 unchecked AC items' },
      { name: 'no_vague_ac', passed: false, detail: 'vague tokens in: implement TBD feature' },
      { name: 'has_file_paths', passed: true, detail: 'ok' },
      { name: 'in_repo_scope', passed: true, detail: 'ok' },
    ]
    render(<NeedsClarityChip checks={checks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    // One merged row labeled "Acceptance criteria"
    expect(screen.getAllByText(/acceptance criteria/i)).toHaveLength(1)
    // Shows the vague-token reason
    expect(screen.getByText(/vague tokens in: implement TBD feature/i)).toBeDefined()
    // Chip is still in failing state (amber), not green
    expect(screen.getByTestId('needs-clarity-chip').className).toMatch(/amber/)
  })
})
