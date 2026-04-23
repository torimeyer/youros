import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FilterDrawer } from './FilterDrawer'
import type { StatusFilter } from './FilterDrawer'

const mockThreads = [
  { id: 't1', name: 'Sprint 1' },
  { id: 't2', name: 'Backlog' },
]

const defaultProps = {
  open: true,
  statusFilter: 'open' as StatusFilter,
  threadFilter: null,
  threads: mockThreads,
  filterCounts: { open: 5, closed: 2, shelved: 1, week: 3 },
  onStatusChange: vi.fn(),
  onThreadChange: vi.fn(),
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

  it('shows all status filter buttons', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('status-filter-open')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-all')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-closed')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-shelved')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-week')).toBeInTheDocument()
    expect(screen.getByTestId('status-filter-recurring')).toBeInTheDocument()
  })

  it('renders Open only and All tasks labels', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('status-filter-open')).toHaveTextContent(/Open only/)
    expect(screen.getByTestId('status-filter-all')).toHaveTextContent(/All tasks/)
  })

  it('calls onStatusChange when a status button is clicked', () => {
    const onStatusChange = vi.fn()
    render(<FilterDrawer {...defaultProps} onStatusChange={onStatusChange} />)
    fireEvent.click(screen.getByTestId('status-filter-closed'))
    expect(onStatusChange).toHaveBeenCalledWith('closed')
  })

  it('does not render view mode toggle buttons', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.queryByTestId('view-mode-list')).not.toBeInTheDocument()
    expect(screen.queryByTestId('view-mode-grid')).not.toBeInTheDocument()
  })

  it('does not render the filter-drawer close button', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.queryByTestId('filter-drawer-close')).not.toBeInTheDocument()
  })

  it('does not render the clear-all button', () => {
    render(<FilterDrawer {...defaultProps} statusFilter="closed" />)
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })
})
