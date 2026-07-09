import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { PRReviewCard, type PRReviewData } from './PRReviewCard'

const BASE: PRReviewData = {
  summary: 'Adds an OAuth login endpoint to the API.',
  file_count: 4,
  additions: 80,
  deletions: 20,
  truncated: false,
  walkthrough: [
    { file: 'api/auth.py', change_type: 'modified', description: 'Adds token validation.' },
    { file: 'api/tests/test_auth.py', change_type: 'modified', description: 'Adds login tests.' },
  ],
  flags: [],
  pr_url: 'https://github.com/acme/repo/pull/42',
  pr_title: 'Add login endpoint',
  pr_number: 42,
  owner: 'acme',
  repo: 'repo',
}

describe('PRReviewCard', () => {
  it('renders the PR number and title as a link', () => {
    render(<PRReviewCard data={BASE} />)
    const link = screen.getByTestId('pr-review-link')
    expect(link).toBeTruthy()
    expect(link.textContent).toContain('#42')
    expect(link.textContent).toContain('Add login endpoint')
    expect(link.getAttribute('href')).toBe('https://github.com/acme/repo/pull/42')
  })

  it('renders the summary', () => {
    render(<PRReviewCard data={BASE} />)
    expect(screen.getByTestId('pr-review-summary').textContent).toContain('OAuth login endpoint')
  })

  it('shows stats row with file count, additions, deletions', () => {
    render(<PRReviewCard data={BASE} />)
    const card = screen.getByTestId('pr-review-card')
    expect(card.textContent).toContain('4 files')
    expect(card.textContent).toContain('+80')
    expect(card.textContent).toContain('-20')
  })

  it('does not show truncation note when truncated=false', () => {
    render(<PRReviewCard data={BASE} />)
    expect(screen.getByTestId('pr-review-card').textContent).not.toContain('partial review')
  })

  it('shows truncation note when truncated=true', () => {
    render(<PRReviewCard data={{ ...BASE, truncated: true }} />)
    expect(screen.getByTestId('pr-review-card').textContent).toContain('partial review')
  })

  it('renders no flags section when flags is empty', () => {
    render(<PRReviewCard data={BASE} />)
    expect(screen.queryByTestId('pr-review-flags')).toBeNull()
  })

  it('renders high-severity flag with correct testid', () => {
    const data = {
      ...BASE,
      flags: [
        { title: 'Auth change', severity: 'high' as const, description: 'Modifies token validation.', file: 'api/auth.py' },
      ],
    }
    render(<PRReviewCard data={data} />)
    expect(screen.getByTestId('pr-flag-high')).toBeTruthy()
    expect(screen.getByTestId('pr-review-flags').textContent).toContain('Auth change')
  })

  it('renders medium and low flags', () => {
    const data = {
      ...BASE,
      flags: [
        { title: 'Test deleted', severity: 'medium' as const, description: 'Removed test file.', file: null },
        { title: 'Naming issue', severity: 'low' as const, description: 'Variable name could be clearer.', file: null },
      ],
    }
    render(<PRReviewCard data={data} />)
    expect(screen.getByTestId('pr-flag-medium').textContent).toContain('Test deleted')
    expect(screen.getByTestId('pr-flag-low').textContent).toContain('Naming issue')
  })

  it('walkthrough is collapsed by default', () => {
    render(<PRReviewCard data={BASE} />)
    expect(screen.queryByTestId('pr-review-walkthrough')).toBeNull()
  })

  it('walkthrough expands on toggle click', () => {
    render(<PRReviewCard data={BASE} />)
    const toggle = screen.getByTestId('pr-review-walkthrough-toggle')
    fireEvent.click(toggle)
    const walkthrough = screen.getByTestId('pr-review-walkthrough')
    expect(walkthrough.textContent).toContain('api/auth.py')
    expect(walkthrough.textContent).toContain('api/tests/test_auth.py')
  })

  it('walkthrough collapses again on second toggle click', () => {
    render(<PRReviewCard data={BASE} />)
    const toggle = screen.getByTestId('pr-review-walkthrough-toggle')
    fireEvent.click(toggle)
    expect(screen.getByTestId('pr-review-walkthrough')).toBeTruthy()
    fireEvent.click(toggle)
    expect(screen.queryByTestId('pr-review-walkthrough')).toBeNull()
  })

  it('renders nothing for walkthrough section when walkthrough is empty', () => {
    render(<PRReviewCard data={{ ...BASE, walkthrough: [] }} />)
    expect(screen.queryByTestId('pr-review-walkthrough-toggle')).toBeNull()
  })
})
