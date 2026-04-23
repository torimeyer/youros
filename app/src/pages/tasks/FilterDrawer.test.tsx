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
  viewMode: 'list' as const,
  threads: mockThreads,
  filterCounts: { open: 5, closed: 2, shelved: 1, week: 3 },
  onStatusChange: vi.fn(),
  onThreadChange: vi.fn(),
  onViewModeChange: vi.fn(),
  onClose: vi.fn(),
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

  it('shows view mode toggle buttons', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('view-mode-list')).toBeInTheDocument()
    expect(screen.getByTestId('view-mode-grid')).toBeInTheDocument()
  })

  it('calls onViewModeChange when view mode is changed', () => {
    const onViewModeChange = vi.fn()
    render(<FilterDrawer {...defaultProps} onViewModeChange={onViewModeChange} />)
    fireEvent.click(screen.getByTestId('view-mode-grid'))
    expect(onViewModeChange).toHaveBeenCalledWith('grid')
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<FilterDrawer {...defaultProps} onClose={onClose} />)
    fireEvent.click(screen.getByTestId('filter-drawer-close'))
    expect(onClose).toHaveBeenCalled()
  })

  it('does not show clear-all button when only default filters are active', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })

  it('shows clear-all button when status filter is non-default', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} statusFilter="closed" onClearAll={onClearAll} />)
    expect(screen.getByTestId('filter-drawer-clear-all')).toBeInTheDocument()
  })

  it('calls onClearAll when clear-all button is clicked', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} statusFilter="closed" onClearAll={onClearAll} />)
    fireEvent.click(screen.getByTestId('filter-drawer-clear-all'))
    expect(onClearAll).toHaveBeenCalled()
  })

  it('does not show clear-all button when onClearAll prop is not provided', () => {
    render(<FilterDrawer {...defaultProps} statusFilter="closed" />)
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })
})
