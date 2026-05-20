import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { GeminiReadyChip } from './GeminiReadyChip'
import type { ReadinessCheck } from './GeminiReadyChip'

vi.mock('./Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`} />,
}))

const mockChecks: ReadinessCheck[] = [
  { name: 'plan_path_present', passed: true, detail: 'found: /path/to/plan.md' },
  { name: 'file_exists', passed: true, detail: '/path/to/plan.md exists' },
  { name: 'has_ac_checkboxes', passed: true, detail: '3 unchecked AC items' },
  { name: 'no_vague_ac', passed: false, detail: 'vague tokens in: Review the API design' },
  { name: 'has_file_paths', passed: true, detail: 'found: api/services/gemini_ready.py' },
  { name: 'ac_count_threshold', passed: true, detail: '3 AC items (need ≥3)' },
  { name: 'referenced_files_exist', passed: true, detail: '2/2 paths resolve' },
  { name: 'in_repo_scope', passed: true, detail: 'task appears scoped to this repo' },
  { name: 'is_unblocked', passed: true, detail: 'no open blockers' },
]

const allPassedChecks: ReadinessCheck[] = mockChecks.map((c) => ({ ...c, passed: true }))

describe('GeminiReadyChip', () => {
  it('renders ready chip with correct testid', () => {
    render(<GeminiReadyChip ready checks={allPassedChecks} />)
    expect(screen.getByTestId('gemini-ready-chip')).toBeDefined()
  })

  it('renders not-ready chip with correct testid', () => {
    render(<GeminiReadyChip ready={false} checks={mockChecks} />)
    expect(screen.getByTestId('gemini-not-ready-chip')).toBeDefined()
  })

  it('tooltip shows ✓ for passed checks and ✗ for failed', () => {
    render(<GeminiReadyChip ready={false} checks={mockChecks} />)
    const tooltip = screen.getByTestId('readiness-tooltip')
    expect(tooltip.textContent).toContain('✓ plan_path_present')
    expect(tooltip.textContent).toContain('✗ no_vague_ac')
  })

  it('tooltip shows all 9 checks on ready chip', () => {
    render(<GeminiReadyChip ready checks={allPassedChecks} />)
    const tooltip = screen.getByTestId('readiness-tooltip')
    expect(tooltip.textContent).toContain('✓ plan_path_present')
    expect(tooltip.textContent).toContain('✓ is_unblocked')
  })

  it('tooltip includes detail strings', () => {
    render(<GeminiReadyChip ready={false} checks={mockChecks} />)
    const tooltip = screen.getByTestId('readiness-tooltip')
    expect(tooltip.textContent).toContain('vague tokens in: Review the API design')
  })

  it('no tooltip element rendered when checks array is empty', () => {
    render(<GeminiReadyChip ready checks={[]} />)
    expect(screen.queryByTestId('readiness-tooltip')).toBeNull()
  })

  it('no tooltip element rendered when checks is undefined', () => {
    render(<GeminiReadyChip ready />)
    expect(screen.queryByTestId('readiness-tooltip')).toBeNull()
  })

  it('calls onClick when ready chip is clicked', () => {
    const onClick = vi.fn()
    render(<GeminiReadyChip ready checks={allPassedChecks} onClick={onClick} />)
    screen.getByTestId('gemini-ready-chip').click()
    expect(onClick).toHaveBeenCalledOnce()
  })
})
