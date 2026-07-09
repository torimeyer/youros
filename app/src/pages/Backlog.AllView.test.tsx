// TDD — RED tests for AllView kanban redesign (→1478)

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
import { buildSpec } from '../lib/spawn'
const mockedGet = vi.mocked(api.get)
const mockedBuildSpec = vi.mocked(buildSpec)

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

  it('RED 1: renders backlog-allview with four kanban columns (→1514)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('backlog-allview')).toBeInTheDocument())
    expect(screen.getByTestId('kanban-column-drafts')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-column-ready-specs')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-column-ready-tasks')).toBeInTheDocument()
    expect(screen.getByTestId('kanban-column-in-progress')).toBeInTheDocument()
    // Use heading role to avoid collision with card status pills that share text with column names
    expect(within(screen.getByTestId('kanban-column-drafts')).getByRole('heading')).toHaveTextContent('Drafts')
    expect(within(screen.getByTestId('kanban-column-ready-specs')).getByRole('heading')).toHaveTextContent('Ready specs')
    expect(within(screen.getByTestId('kanban-column-ready-tasks')).getByRole('heading')).toHaveTextContent('Ready tasks')
    expect(within(screen.getByTestId('kanban-column-in-progress')).getByRole('heading')).toHaveTextContent('In progress')
  })

  it('RED 2: draft spec appears in kanban-column-drafts with type chip "Spec"', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-draft-spec')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-drafts')
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

  it('RED 4: open task appears in kanban-column-ready-tasks with type chip "Task"', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-open-task')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-ready-tasks')
    expect(col).toContainElement(screen.getByTestId('kanban-card-open-task'))
    const card = screen.getByTestId('kanban-card-open-task')
    expect(card.querySelector('[data-testid="card-type-chip"]')).toHaveTextContent('Task')
  })

  it('RED 4b: ready spec appears in kanban-column-ready-specs (→1514)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('kanban-card-ready-spec')).toBeInTheDocument())
    const col = screen.getByTestId('kanban-column-ready-specs')
    expect(col).toContainElement(screen.getByTestId('kanban-card-ready-spec'))
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

  it('RED 9: filter-chip-specs-only does NOT exist (redundant with top nav)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('backlog-allview')).toBeInTheDocument())
    expect(screen.queryByTestId('filter-chip-all')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-chip-specs-only')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-chip-tasks-only')).not.toBeInTheDocument()
  })

  it('RED 10: filter-chip-stale does NOT exist (removed in →1481)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('backlog-allview')).toBeInTheDocument())
    expect(screen.queryByTestId('filter-chip-stale')).not.toBeInTheDocument()
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

  it('RED 10: clicking Build button calls buildSpec with the spec path (→1482)', async () => {
    mockedBuildSpec.mockResolvedValueOnce({ status: 'ok', agents: [], message: 'launched' })
    setup()
    await waitFor(() => expect(screen.getByTestId('card-build-button')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('card-build-button'))
    await waitFor(() => expect(mockedBuildSpec).toHaveBeenCalledWith(READY_SPEC.path))
  })

  it('RED 11: Build button has rounded-md class (button, not pill) (→1482)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('card-build-button')).toBeInTheDocument())
    const btn = screen.getByTestId('card-build-button')
    expect(btn.className).toContain('rounded-md')
    expect(btn.className).not.toContain('rounded-full')
  })

  it('RED 12: Build button has px-4 py-2 text-sm for clear button affordance (→1482)', async () => {
    setup()
    await waitFor(() => expect(screen.getByTestId('card-build-button')).toBeInTheDocument())
    const btn = screen.getByTestId('card-build-button')
    expect(btn.className).toContain('px-4')
    expect(btn.className).toContain('py-2')
    expect(btn.className).toContain('text-sm')
  })
})
