import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FilterDrawer } from './FilterDrawer'
import type { StatusFilter } from './FilterDrawer'

const mockLabels = [
  { id: 'l1', name: 'Bug', color: '#ef4444', task_count: 2 },
  { id: 'l2', name: 'Docs', color: '#3b82f6', task_count: 1 },
]

const mockThreads = [
  { id: 't1', name: 'Sprint 1' },
  { id: 't2', name: 'Backlog' },
]

const defaultProps = {
  open: true,
  statusFilter: 'open' as StatusFilter,
  priorityFilter: null,
  labelFilter: null,
  threadFilter: null,
  hideSessionTasks: true,
  viewMode: 'list' as const,
  labels: mockLabels,
  threads: mockThreads,
  filterCounts: { open: 5, closed: 2, shelved: 1, week: 3 },
  priorityCounts: { P0: 1, P1: 2, P2: 3, P3: 0 },
  onStatusChange: vi.fn(),
  onPriorityChange: vi.fn(),
  onLabelChange: vi.fn(),
  onThreadChange: vi.fn(),
  onSessionToggle: vi.fn(),
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

  it('shows all priority filter chips', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('priority-filter-P0')).toBeInTheDocument()
    expect(screen.getByTestId('priority-filter-P1')).toBeInTheDocument()
    expect(screen.getByTestId('priority-filter-P2')).toBeInTheDocument()
    expect(screen.getByTestId('priority-filter-P3')).toBeInTheDocument()
  })

  it('calls onPriorityChange when a priority chip is clicked', () => {
    const onPriorityChange = vi.fn()
    render(<FilterDrawer {...defaultProps} onPriorityChange={onPriorityChange} />)
    fireEvent.click(screen.getByTestId('priority-filter-P0'))
    expect(onPriorityChange).toHaveBeenCalledWith('P0')
  })

  it('toggles off priority filter when the active priority is clicked again', () => {
    const onPriorityChange = vi.fn()
    render(<FilterDrawer {...defaultProps} priorityFilter="P1" onPriorityChange={onPriorityChange} />)
    fireEvent.click(screen.getByTestId('priority-filter-P1'))
    expect(onPriorityChange).toHaveBeenCalledWith(null)
  })

  it('shows label filter chips', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('label-filter-l1')).toBeInTheDocument()
    expect(screen.getByTestId('label-filter-l2')).toBeInTheDocument()
  })

  it('calls onLabelChange when a label chip is clicked', () => {
    const onLabelChange = vi.fn()
    render(<FilterDrawer {...defaultProps} onLabelChange={onLabelChange} />)
    fireEvent.click(screen.getByTestId('label-filter-l1'))
    expect(onLabelChange).toHaveBeenCalledWith('l1')
  })

  it('shows clear-label button when a label is active, and calls onLabelChange(null)', () => {
    const onLabelChange = vi.fn()
    render(<FilterDrawer {...defaultProps} labelFilter="l1" onLabelChange={onLabelChange} />)
    const clearBtn = screen.getByTitle('Clear label filter')
    fireEvent.click(clearBtn)
    expect(onLabelChange).toHaveBeenCalledWith(null)
  })

  it('shows sessions toggle button', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.getByTestId('toggle-session-tasks')).toBeInTheDocument()
  })

  it('calls onSessionToggle when sessions button is clicked', () => {
    const onSessionToggle = vi.fn()
    render(<FilterDrawer {...defaultProps} onSessionToggle={onSessionToggle} />)
    fireEvent.click(screen.getByTestId('toggle-session-tasks'))
    expect(onSessionToggle).toHaveBeenCalled()
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

  it('does not show label section when labels array is empty', () => {
    render(<FilterDrawer {...defaultProps} labels={[]} />)
    expect(screen.queryByTestId('label-filter-l1')).not.toBeInTheDocument()
  })

  it('does not show clear-all button when only default filters are active', () => {
    render(<FilterDrawer {...defaultProps} />)
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })

  it('shows clear-all button when priority filter is active', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} priorityFilter="P1" onClearAll={onClearAll} />)
    expect(screen.getByTestId('filter-drawer-clear-all')).toBeInTheDocument()
  })

  it('shows clear-all button when label filter is active', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} labelFilter="l1" onClearAll={onClearAll} />)
    expect(screen.getByTestId('filter-drawer-clear-all')).toBeInTheDocument()
  })

  it('shows clear-all button when status filter is non-default', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} statusFilter="closed" onClearAll={onClearAll} />)
    expect(screen.getByTestId('filter-drawer-clear-all')).toBeInTheDocument()
  })

  it('calls onClearAll when clear-all button is clicked', () => {
    const onClearAll = vi.fn()
    render(<FilterDrawer {...defaultProps} priorityFilter="P0" onClearAll={onClearAll} />)
    fireEvent.click(screen.getByTestId('filter-drawer-clear-all'))
    expect(onClearAll).toHaveBeenCalled()
  })

  it('does not show clear-all button when onClearAll prop is not provided', () => {
    render(<FilterDrawer {...defaultProps} priorityFilter="P0" />)
    expect(screen.queryByTestId('filter-drawer-clear-all')).not.toBeInTheDocument()
  })
})
