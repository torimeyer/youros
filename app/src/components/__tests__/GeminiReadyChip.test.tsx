import { describe, it, expect, vi } from 'vitest'
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
  it('renders green chip when ready', () => {
    render(<GeminiReadyChip ready={true} checks={passingChecks} />)
    expect(screen.getByTestId('gemini-ready-chip')).toBeInTheDocument()
  })

  it('renders not-ready chip when not ready', () => {
    render(<GeminiReadyChip ready={false} checks={failingChecks} />)
    expect(screen.getByTestId('gemini-not-ready-chip')).toBeInTheDocument()
  })

  it('calls onClick when ready chip is clicked', async () => {
    const onClick = vi.fn()
    render(<GeminiReadyChip ready={true} checks={passingChecks} onClick={onClick} />)
    await userEvent.click(screen.getByTestId('gemini-ready-chip'))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('not-ready chip shows tooltip with failed check names', () => {
    render(<GeminiReadyChip ready={false} checks={failingChecks} />)
    const chip = screen.getByTestId('gemini-not-ready-chip')
    const title = chip.getAttribute('title') ?? ''
    expect(title).toContain('has_ac_checkboxes')
    expect(title).toContain('has_file_paths')
  })

  it('ready chip does not call onClick without handler', async () => {
    render(<GeminiReadyChip ready={true} checks={passingChecks} />)
    await expect(
      userEvent.click(screen.getByTestId('gemini-ready-chip'))
    ).resolves.not.toThrow()
  })

  it('renders with no checks prop', () => {
    render(<GeminiReadyChip ready={false} />)
    expect(screen.getByTestId('gemini-not-ready-chip')).toBeInTheDocument()
  })
})
