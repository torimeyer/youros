import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { NeedsClarityChip, type ReadinessCheck } from './NeedsClarityChip'

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
})
