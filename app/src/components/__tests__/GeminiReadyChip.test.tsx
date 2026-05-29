// GeminiReadyChip was renamed to NeedsClarityChip (→1515).
// GeminiReadyChip.tsx re-exports NeedsClarityChip as GeminiReadyChip for compat.
// Tests updated to match current interface: no `ready`/`onClick` props.
// A ready spec (all checks pass) renders nothing here on purpose (needs-clarity
// demotion, →1515): the ready state is shown by a single status pill
// elsewhere, not by this chip. The chip only appears when clarity is needed.
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GeminiReadyChip } from '../GeminiReadyChip'

const passingChecks = [
  { name: 'plan_path_present', passed: true, detail: 'Found ~/.claude/plans/foo.md' },
  { name: 'file_exists', passed: true, detail: 'File exists' },
  { name: 'has_ac_checkboxes', passed: true, detail: '3 checkboxes found' },
  { name: 'no_vague_ac', passed: true, detail: 'No vague tokens' },
  { name: 'has_file_paths', passed: true, detail: '2 file paths found' },
  { name: 'is_unblocked', passed: true, detail: 'All blockers resolved' },
]

const failingChecks = [
  { name: 'plan_path_present', passed: true, detail: 'Found ~/.claude/plans/foo.md' },
  { name: 'file_exists', passed: true, detail: 'File exists' },
  { name: 'has_ac_checkboxes', passed: false, detail: 'No checkboxes found' },
  { name: 'no_vague_ac', passed: true, detail: 'No vague tokens' },
  { name: 'has_file_paths', passed: false, detail: 'No file paths found' },
  { name: 'is_unblocked', passed: true, detail: 'All blockers resolved' },
]

describe('GeminiReadyChip', () => {
  it('renders nothing when all checks pass (ready shows one status pill, not a chip)', () => {
    const { container } = render(<GeminiReadyChip checks={passingChecks} />)
    expect(screen.queryByTestId('needs-clarity-chip')).not.toBeInTheDocument()
    expect(container.firstChild).toBeNull()
  })

  it('renders not-ready chip when some checks fail', () => {
    render(<GeminiReadyChip checks={failingChecks} />)
    const chip = screen.getByTestId('needs-clarity-chip')
    expect(chip).toBeInTheDocument()
    expect(screen.getByTestId('needs-clarity-tooltip')).toBeInTheDocument()
  })

  it('clicking not-ready chip opens the detail modal', async () => {
    render(<GeminiReadyChip checks={failingChecks} />)
    await userEvent.click(screen.getByTestId('needs-clarity-chip'))
    expect(screen.getByTestId('needs-clarity-modal')).toBeInTheDocument()
  })

  it('not-ready chip shows tooltip with failed check names', () => {
    render(<GeminiReadyChip checks={failingChecks} />)
    const chip = screen.getByTestId('needs-clarity-chip')
    const title = chip.getAttribute('title') ?? ''
    expect(title).toContain("Steps to verify it's done")
    expect(title).toContain('Names the files it touches')
  })

  it('renders nothing when no checks prop is passed', () => {
    const { container } = render(<GeminiReadyChip />)
    expect(container.firstChild).toBeNull()
  })
})
