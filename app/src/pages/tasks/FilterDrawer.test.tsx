import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FilterDrawer } from './FilterDrawer'
import type { StatusFilter } from './FilterDrawer'

const mockThreads = [
  { id: 't1', name: 'Sprint 1' },
  { id: 't2', name: 'Backlog' },
]

const allStatuses = new Set<StatusFilter>(['open', 'in_progress', 'closed'])

const defaultProps = {
  open: true,
  selectedStatuses: allStatuses,
  threadFilter: null,
  threads: mockThreads,
  filterCounts: { open: 5, in_progress: 2, closed: 2 },
  onStatusToggle: vi.fn(),
  onThreadChange: vi.fn(),
  onSortByChange: vi.fn(),
}

describe('FilterDrawer', () => {
  it('renders when open=true', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('filter-drawer')).toBeInTheDocument()
  })

  it('still renders when open=false (filters are always visible)', () => {
    render(<FilterDrawer {...defaultProps} open={false} />)
    expect(screen.getByTestId('filter-drawer')).toBeInTheDocument()
  })

  it('shows only the three real status pills: Open, In progress, Closed', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('status-filter-open')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-in_progress')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-closed')).toBeInTheDocument()
    // Regression: no "all", "shelved", "week", "recurring" pills.
    expect(screen.queryByTestId('status-filter-all')).not.toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-shelved')).not.toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-week')).not.toBeInTheDocument()
    expect(screen.queryByTestId('status-filter-recurring')).not.toBeInTheDocument()
  })

  it('pills use plain-language labels', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('status-filter-open')).toHaveTextContent(/^Open/)
    expect(screen.getByTestId('status-filter-in_progress')).toHaveTextContent(
      /^In progress/
    )
    expect(screen.getByTestId('status-filter-closed')).toHaveTextContent(/^Closed/)
  })

  it('all three pills appear selected (aria-pressed=true) when all statuses are selected', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('status-filter-open')).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByTestId('status-filter-in_progress')).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByTestId('status-filter-closed')).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('clicking a pill calls onStatusToggle with that status', () => {
    const onStatusToggle = vi.fn()
    render(<FilterDrawer {...defaultProps} onStatusToggle={onStatusToggle} />)
    fireEvent.click(screen.getByTestId('status-filter-closed'))
    expect(onStatusToggle).toHaveBeenCalledWith('closed')
  })

  it('renders exactly ONE sort row (not two) - regression for duplicate sort bug', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getAllByTestId('filter-drawer-sort-row')).toHaveLength(1)
  })

  it('renders the four sort-by options', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('sort-by-date-desc')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-date-asc')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-status')).toBeInTheDocument()
    expect(screen.getByTestId('sort-by-label')).toBeInTheDocument()
  })

  it('does not render view mode toggle buttons or filter-drawer close button - regression for card/view-toggle toolbar', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.queryByTestId('view-mode-list')).not.toBeInTheDocument()
    expect(screen.queryByTestId('view-mode-grid')).not.toBeInTheDocument()
    expect(screen.queryByTestId('view-mode-toggle')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-drawer-close')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/list view/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/grid view/i)).not.toBeInTheDocument()
  })
})
