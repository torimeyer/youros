// TDD — RED tests for AllView kanban redesign (→1478)
// See plan: /Users/torimeyer/.claude/plans/this-isnt-very-user-twinkling-yeti.md

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Backlog from './Backlog'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn() },
}))
vi.mock('./Specs', () => ({ default: () => <div data-testid="specs-page">Specs</div> }))
vi.mock('./Tasks', () => ({ default: () => <div data-testid="tasks-page">Tasks</div> }))
vi.mock('../lib/spawn', () => ({ buildSpec: vi.fn() }))
vi.mock('../components/ConflictDialog', () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="conflict-dialog">Conflict</div> : null,
}))

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

const DRAFT_SPEC = { id: 'draft-spec', path: 'docs/draft/d.md', title: 'Draft Spec Title', status: 'draft', task_ids: [] }
const READY_SPEC = { id: 'ready-spec', path: 'docs/spec/r.md', title: 'Ready Spec Title', status: 'ready', task_ids: ['task-a', 'task-b'] }
const OPEN_TASK = { id: 'open-task', title: 'Open Task Title', status: 'open', spec_id: null }
const IN_PROGRESS_TASK = { id: 'inprogress-task', title: 'In Progress Task Title', status: 'in_progress', spec_id: null }

function setup(
  specs = [DRAFT_SPEC, READY_SPEC],
  tasks = [OPEN_TASK, IN_PROGRESS_TASK],
) {
  mockedGet.mockImplementation((url: string) => {
    if (url === '/specs') return Promise.resolve({ docs: specs })
    if (url === '/tasks') return Promise.resolve({ tasks })
    return Promise.resolve({})
  })
  return render(
    <MemoryRouter initialEntries={['/backlog']}>
      <Backlog />
    </MemoryRouter>,
  )
}

describe('AllView kanban (→1478)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('RED 1: renders backlog-allview with three kanban columns', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('backlog-allview')).toBeInTheDocument())
    expect(screen.getByTestId('kanban-column-drafting')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-column-ready')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-column-in-progress')).toBeInTheDocument()
    // Use heading role to avoid collision with card status pills that share text with column names
    expect(within(screen.getByTestId('kanban-column-drafting')).getByRole('heading')).toHaveTextContent('Drafting')
    expect(within(screen.getByTestId('kanban-column-ready')).getByRole('heading')).toHaveTextContent('Ready')
    expect(within(screen.getByTestId('kanban-column-in-progress')).getByRole('heading')).toHaveTextContent('In progress')
  })

  it('RED 2: draft spec appears in kanban-column-drafting with type chip "Spec"', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-draft-spec')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-drafting')
    expect(col).toContainElement(screen.getByTestId('kanban-card-draft-spec'))
    const card = screen.getByTestId('kanban-card-draft-spec')
    expect(card.querySelector('[data-testid="card-type-chip"]')).toHaveTextContent('Spec')
  })

  it('RED 3: ready spec renders card-build-button; draft spec does not', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-ready-spec')).toBeInTheDocument())
    const readyCard = screen.getByTestId('kanban-card-ready-spec')
    expect(readyCard.querySelector('[data-testid="card-build-button"]')).toBeInTheDocument()
    const draftCard = screen.getByTestId('kanban-card-draft-spec')
    expect(draftCard.querySelector('[data-testid="card-build-button"]')).toBeNull()
  })

  it('RED 4: open task appears in kanban-column-drafting with type chip "Task"', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-open-task')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-drafting')
    expect(col).toContainElement(screen.getByTestId('kanban-card-open-task'))
    const card = screen.getByTestId('kanban-card-open-task')
    expect(card.querySelector('[data-testid="card-type-chip"]')).toHaveTextContent('Task')
  })

  it('RED 5: in_progress task appears in kanban-column-in-progress', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-inprogress-task')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-in-progress')
    expect(col).toContainElement(screen.getByTestId('kanban-card-inprogress-task'))
  })

  it('RED 6: spec with task_ids renders card-task-chip for each id', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-ready-spec')).toBeInTheDocument())
    const card = screen.getByTestId('kanban-card-ready-spec')
    expect(card.querySelector('[data-testid="card-task-chip-task-a"]')).toBeInTheDocument()
    expect(card.querySelector('[data-testid="card-task-chip-task-b"]')).toBeInTheDocument()
  })

  it('RED 7: filter-chip-specs-only hides task cards across all columns', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('filter-chip-specs-only')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('filter-chip-specs-only'))
    await waitFor(() => {
      expect(screen.queryByTestId('kanban-card-open-task')).not.toBeInTheDocument()
      expect(screen.queryByTestId('kanban-card-inprogress-task')).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('kanban-card-draft-spec')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-card-ready-spec')).toBeInTheDocument()
  })

  it('RED 8: filter-search-input filters cards by title substring', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('filter-search-input')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('filter-search-input'), { target: { value: 'Draft Spec' } })
    await waitFor(() => {
      expect(screen.getByTestId('kanban-card-draft-spec')).toBeInTheDocument()
      expect(screen.queryByTestId('kanban-card-ready-spec')).not.toBeInTheDocument()
      expect(screen.queryByTestId('kanban-card-open-task')).not.toBeInTheDocument()
    })
  })
})
