import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NeedsClarityChip, type ReadinessCheck } from './NeedsClarityChip'

vi.mock('../lib/api', () => ({
  api: {
    patch: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockedApiPatch = vi.mocked(api.patch)

const failedChecks: ReadinessCheck[] = [
  { name: 'plan_path_present', passed: true, detail: 'ok' },
  { name: 'file_exists', passed: true, detail: 'ok' },
  { name: 'has_ac_checkboxes', passed: false, detail: 'no checkboxes found' },
  { name: 'no_vague_ac', passed: true, detail: 'ok' },
  { name: 'has_file_paths', passed: false, detail: 'no file paths found' },
  { name: 'ac_count_threshold', passed: false, detail: '1 AC items (need ≥3)' },
  { name: 'referenced_files_exist', passed: false, detail: 'not evaluated' },
  { name: 'in_repo_scope', passed: true, detail: 'ok' },
  { name: 'is_unblocked', passed: true, detail: 'no blockers' },
]

const allPassedChecks: ReadinessCheck[] = failedChecks.map((c) => ({ ...c, passed: true, detail: 'ok' }))

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

  it('modal lists all 9 checks with pass/fail icons', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    // Failing check labels should appear
    expect(screen.getByText(/has acceptance criteria/i)).toBeDefined()
    expect(screen.getByText(/references real files/i)).toBeDefined()
  })

  it('modal shows textarea for each failing check', () => {
    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))
    // has_ac_checkboxes is failing — should have a textarea
    expect(screen.getByTestId('clarity-input-has_ac_checkboxes')).toBeDefined()
  })

  it('saving a clarity fix calls PATCH endpoint and refreshes checks', async () => {
    const updatedChecks = failedChecks.map((c) =>
      c.name === 'has_ac_checkboxes' ? { ...c, passed: true, detail: 'fixed' } : c
    )
    mockedApiPatch.mockResolvedValueOnce({ checks: updatedChecks, ready: false })

    render(<NeedsClarityChip checks={failedChecks} specPath="docs/draft/foo.md" />)
    fireEvent.click(screen.getByTestId('needs-clarity-chip'))

    const textarea = screen.getByTestId('clarity-input-has_ac_checkboxes')
    fireEvent.change(textarea, { target: { value: '- [ ] add login button\n- [ ] add logout button\n- [ ] handle errors' } })

    const saveBtn = screen.getByTestId('clarity-save-has_ac_checkboxes')
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockedApiPatch).toHaveBeenCalledWith(
        '/api/specs/docs/draft/foo.md/clarity',
        { check: 'has_ac_checkboxes', fix: expect.stringContaining('login button') }
      )
    })
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
})
