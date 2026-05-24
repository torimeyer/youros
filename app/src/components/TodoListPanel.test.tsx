import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { TodoListPanel } from './TodoListPanel'
import type { Todo } from './TodoListPanel'

describe('TodoListPanel', () => {
  it('renders nothing when todos is empty', () => {
    const { container } = render(<TodoListPanel todos={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders panel with testid when todos exist', () => {
    const todos: Todo[] = [{ subject: 'Do something', status: 'pending' }]
    render(<TodoListPanel todos={todos} />)
    expect(screen.getByTestId('todo-list-panel')).toBeTruthy()
  })

  it('renders pending status with ▸ icon', () => {
    const todos: Todo[] = [{ subject: 'Pending task', status: 'pending' }]
    render(<TodoListPanel todos={todos} />)
    const item = screen.getByTestId('todo-item-pending')
    expect(item).toBeTruthy()
    expect(item.textContent).toContain('▸')
    expect(item.textContent).toContain('Pending task')
  })

  it('renders in_progress status with ◔ icon and activeForm label', () => {
    const todos: Todo[] = [
      { subject: 'Running task', status: 'in_progress', activeForm: 'Running the task' },
    ]
    render(<TodoListPanel todos={todos} />)
    const item = screen.getByTestId('todo-item-in_progress')
    expect(item).toBeTruthy()
    expect(item.textContent).toContain('◔')
    expect(item.textContent).toContain('Running the task')
  })

  it('renders in_progress with subject when no activeForm', () => {
    const todos: Todo[] = [{ subject: 'Active task', status: 'in_progress' }]
    render(<TodoListPanel todos={todos} />)
    const item = screen.getByTestId('todo-item-in_progress')
    expect(item.textContent).toContain('Active task')
  })

  it('renders completed status with ✓ icon', () => {
    const todos: Todo[] = [{ subject: 'Done task', status: 'completed' }]
    render(<TodoListPanel todos={todos} />)
    const item = screen.getByTestId('todo-item-completed')
    expect(item).toBeTruthy()
    expect(item.textContent).toContain('✓')
    expect(item.textContent).toContain('Done task')
  })

  it('renders multiple todos with correct testids', () => {
    const todos: Todo[] = [
      { subject: 'Task 1', status: 'completed' },
      { subject: 'Task 2', status: 'in_progress' },
      { subject: 'Task 3', status: 'pending' },
    ]
    render(<TodoListPanel todos={todos} />)
    expect(screen.getByTestId('todo-item-completed')).toBeTruthy()
    expect(screen.getByTestId('todo-item-in_progress')).toBeTruthy()
    expect(screen.getByTestId('todo-item-pending')).toBeTruthy()
  })
})
