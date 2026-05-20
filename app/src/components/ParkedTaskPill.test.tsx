import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import ParkedTaskPill, { type ParkedTask } from './ParkedTaskPill'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: { delete: vi.fn().mockResolvedValue({}) },
}))

const makeTask = (overrides: Partial<ParkedTask> = {}): ParkedTask => ({
  id: 'task-1',
  tab_id: 'tab-1',
  label: 'Wait for build',
  kind: 'schedule',
  wakeup_at: Date.now() / 1000 + 60,
  created_at: Date.now() / 1000,
  ...overrides,
})

describe('ParkedTaskPill', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nothing when tasks list is empty', () => {
    const { container } = render(<ParkedTaskPill tasks={[]} onCancelled={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders a pill for a scheduled task', () => {
    render(<ParkedTaskPill tasks={[makeTask()]} onCancelled={vi.fn()} />)
    expect(screen.getByTestId('parked-pill-task-1')).toBeInTheDocument()
    expect(screen.getByText('Wait for build')).toBeInTheDocument()
  })

  it('renders "Monitoring..." for monitor-kind tasks', () => {
    const task = makeTask({ id: 'task-m', kind: 'monitor', wakeup_at: null })
    render(<ParkedTaskPill tasks={[task]} onCancelled={vi.fn()} />)
    expect(screen.getByText(/Monitoring/)).toBeInTheDocument()
  })

  it('cancel button calls DELETE endpoint and onCancelled', async () => {
    const onCancelled = vi.fn()
    render(<ParkedTaskPill tasks={[makeTask()]} onCancelled={onCancelled} />)
    await act(async () => {
      fireEvent.click(screen.getByTestId('cancel-pill-task-1'))
    })
    expect(api.delete).toHaveBeenCalledWith('/api/parked_tasks/task-1')
    expect(onCancelled).toHaveBeenCalledWith('task-1')
  })

  it('shows 3 pills inline and overflow button when 4+ tasks', () => {
    const tasks = [1, 2, 3, 4].map(i =>
      makeTask({ id: `t-${i}`, label: `Task ${i}` })
    )
    render(<ParkedTaskPill tasks={tasks} onCancelled={vi.fn()} />)
    expect(screen.getByTestId('parked-pill-t-1')).toBeInTheDocument()
    expect(screen.getByTestId('parked-pill-t-2')).toBeInTheDocument()
    expect(screen.getByTestId('parked-pill-t-3')).toBeInTheDocument()
    expect(screen.queryByTestId('parked-pill-t-4')).not.toBeInTheDocument()
    expect(screen.getByTestId('parked-overflow-button')).toBeInTheDocument()
  })

  it('clicking overflow button opens popover with remaining tasks', () => {
    const tasks = [1, 2, 3, 4].map(i =>
      makeTask({ id: `t-${i}`, label: `Task ${i}` })
    )
    render(<ParkedTaskPill tasks={tasks} onCancelled={vi.fn()} />)
    fireEvent.click(screen.getByTestId('parked-overflow-button'))
    expect(screen.getByTestId('parked-overflow-popover')).toBeInTheDocument()
    expect(screen.getByTestId('parked-pill-t-4')).toBeInTheDocument()
  })

  it('pill disappears when task_cancelled arrives (via onCancelled prop)', () => {
    const tasks = [makeTask()]
    const { rerender } = render(
      <ParkedTaskPill tasks={tasks} onCancelled={vi.fn()} />
    )
    expect(screen.getByTestId('parked-pill-task-1')).toBeInTheDocument()
    rerender(<ParkedTaskPill tasks={[]} onCancelled={vi.fn()} />)
    expect(screen.queryByTestId('parked-pill-task-1')).not.toBeInTheDocument()
  })

  it('pill appears when task_parked arrives (via tasks prop)', () => {
    const { rerender } = render(
      <ParkedTaskPill tasks={[]} onCancelled={vi.fn()} />
    )
    expect(screen.queryByTestId('parked-pills-row')).not.toBeInTheDocument()
    rerender(<ParkedTaskPill tasks={[makeTask()]} onCancelled={vi.fn()} />)
    expect(screen.getByTestId('parked-pills-row')).toBeInTheDocument()
  })
})
