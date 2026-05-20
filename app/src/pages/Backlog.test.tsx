import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Backlog from './Backlog'


vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}))


vi.mock('../lib/spawn', () => ({
  buildSpec: vi.fn(),
}))

vi.mock('../components/ConflictDialog', () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="conflict-dialog">Conflict</div> : null,
}))

import { api } from '../lib/api'
const mockedApiGet = vi.mocked(api.get)

const SAMPLE_SPECS = [
  { id: 's1', title: 'Build auth module', status: 'ready', task_ids: ['t1', 't2'] },
  { id: 's2', title: 'Redesign dashboard', status: 'draft', task_ids: [] },
]

const SAMPLE_TASKS = [
  { id: 't1', title: 'Write tests', status: 'open', spec_id: 's1' },
  { id: 't2', title: 'Implement login', status: 'open', spec_id: 's1' },
  { id: 't3', title: 'Fix header bug', status: 'open', spec_id: null },
  { id: 't4', title: 'Update README', status: 'open', spec_id: null },
]

const TASK_WITH_DESCRIPTION = {
  id: 'td1',
  title: 'Short title',
  status: 'open',
  spec_id: null,
  description: 'This is a detailed description of what needs to be done.',
}

const SPEC_WITH_DESCRIPTION = {
  id: 'sd1',
  title: 'Spec with details',
  status: 'draft',
  task_ids: [],
  description: 'Spec description explaining the why and acceptance criteria.',
}

function renderBacklog(path = '/backlog') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Backlog />
    </MemoryRouter>
  )
}

// →1468: verify AllView handles the REAL API response shapes.
// /tasks returns {tasks:[...]} and /specs returns {docs:[...]}.
// Before the fix, AllView passed the raw object to state and tasks.filter crashed.
describe('Backlog AllView — real API response shapes (→1468)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: SAMPLE_SPECS })
      if (url === '/tasks') return Promise.resolve({ tasks: SAMPLE_TASKS })
      return Promise.resolve([])
    })
  })

  it('renders without crashing when API returns wrapped objects', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('backlog-allview')).toBeInTheDocument()
    })
  })

  it('shows spec titles when API returns {docs:[...]}', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Build auth module')).toBeInTheDocument()
      expect(screen.getByText('Redesign dashboard')).toBeInTheDocument()
    })
  })

  it('shows standalone tasks when API returns {tasks:[...]}', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Fix header bug')).toBeInTheDocument()
      expect(screen.getByText('Update README')).toBeInTheDocument()
    })
  })

  it('shows all open tasks (including spec-linked) in the kanban ready column', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-ready-tasks')).toBeInTheDocument()
    })
    // All open tasks appear in Ready column (Patterson step 3)
    expect(screen.getByText('Write tests')).toBeInTheDocument()
    expect(screen.getByText('Implement login')).toBeInTheDocument()
  })
})

describe('Backlog (Kanban view) — no sub-tabs (→1489)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: SAMPLE_SPECS })
      if (url === '/tasks') return Promise.resolve({ tasks: SAMPLE_TASKS })
      return Promise.resolve({})
    })
  })

  it('renders the kanban AllView directly — no sub-tab nav', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('backlog-allview')).toBeInTheDocument()
    })
    expect(screen.queryByRole('link', { name: 'All' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Specs' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Tasks' })).toBeNull()
  })

  it('shows kanban Drafting column', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-drafts')).toBeInTheDocument()
    })
  })

  it('shows kanban In progress column', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-in-progress')).toBeInTheDocument()
    })
  })

  it('renders spec titles', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText('Build auth module')).toBeInTheDocument()
      expect(screen.getByText('Redesign dashboard')).toBeInTheDocument()
    })
  })

  it('renders a Build button only for Ready specs', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText('Build auth module')).toBeInTheDocument()
    })
    const buildButtons = screen.getAllByRole('button', { name: /build/i })
    expect(buildButtons).toHaveLength(1)
  })

  it('renders standalone tasks', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText('Fix header bug')).toBeInTheDocument()
      expect(screen.getByText('Update README')).toBeInTheDocument()
    })
  })

  it('shows all open tasks in the kanban ready column (including spec-linked)', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-ready-tasks')).toBeInTheDocument()
    })
    expect(screen.getByText('Write tests')).toBeInTheDocument()
    expect(screen.getByText('Implement login')).toBeInTheDocument()
  })
})

describe('Backlog card description expand/collapse (Patterson step 1)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('task card with description shows collapsed preview and Show more button', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [TASK_WITH_DESCRIPTION] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Short title')).toBeInTheDocument()
    })
    expect(screen.getByText('This is a detailed description of what needs to be done.')).toBeInTheDocument()
    expect(screen.getByTestId('card-expand')).toHaveTextContent('Show more')
  })

  it('clicking Show more on a task card expands and changes button to Show less', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [TASK_WITH_DESCRIPTION] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('card-expand')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('card-expand'))
    expect(screen.getByTestId('card-expand')).toHaveTextContent('Show less')
  })

  it('task card without description renders without a Show more button', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [SAMPLE_TASKS[0]] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Write tests')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('card-expand')).toBeNull()
  })

  it('spec card with description shows collapsed preview and Show more button', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [SPEC_WITH_DESCRIPTION] })
      if (url === '/tasks') return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Spec with details')).toBeInTheDocument()
    })
    expect(screen.getByText('Spec description explaining the why and acceptance criteria.')).toBeInTheDocument()
    expect(screen.getByTestId('card-expand')).toHaveTextContent('Show more')
  })

  it('spec card without description renders without a Show more button', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [SAMPLE_SPECS[1]] })
      if (url === '/tasks') return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Redesign dashboard')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('card-expand')).toBeNull()
  })
})

describe('Backlog column count badges (→1487)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('drafting column badge shows count matching number of draft specs', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [
        { id: 's1', title: 'Spec A', status: 'draft', task_ids: [] },
        { id: 's2', title: 'Spec B', status: 'draft', task_ids: [] },
        { id: 's3', title: 'Spec C', status: 'draft', task_ids: [] },
      ]})
      if (url === '/tasks') return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('column-count-drafts')).toBeInTheDocument()
    })
    expect(screen.getByTestId('column-count-drafts')).toHaveTextContent('3')
  })

  it('empty drafting column badge shows 0', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('column-count-drafts')).toBeInTheDocument()
    })
    expect(screen.getByTestId('column-count-drafts')).toHaveTextContent('0')
  })

  it('ready column badge shows combined count of ready specs plus open tasks', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [
        { id: 's1', title: 'Ready spec 1', status: 'ready', task_ids: [] },
        { id: 's2', title: 'Ready spec 2', status: 'ready', task_ids: [] },
      ]})
      if (url === '/tasks') return Promise.resolve({ tasks: [
        { id: 't1', title: 'Task 1', status: 'open', spec_id: null },
        { id: 't2', title: 'Task 2', status: 'open', spec_id: null },
        { id: 't3', title: 'Task 3', status: 'open', spec_id: null },
        { id: 't4', title: 'Task 4', status: 'open', spec_id: null },
      ]})
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('column-count-ready-specs')).toBeInTheDocument()
    })
    expect(screen.getByTestId('column-count-ready-specs')).toHaveTextContent('2')
    expect(screen.getByTestId('column-count-ready-tasks')).toHaveTextContent('4')
  })
})

describe('Backlog column semantics (Patterson step 3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('Drafting column renders only draft specs, no tasks', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [SAMPLE_SPECS[1]] })
      if (url === '/tasks') return Promise.resolve({ tasks: [SAMPLE_TASKS[0]] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-drafts')).toBeInTheDocument()
    })
    const draftingCol = screen.getByTestId('kanban-column-drafts')
    expect(within(draftingCol).getByText('Redesign dashboard')).toBeInTheDocument()
    expect(within(draftingCol).queryByText('Write tests')).toBeNull()
  })

  it('Ready column renders both ready specs and open tasks', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [SAMPLE_SPECS[0]] })
      if (url === '/tasks') return Promise.resolve({ tasks: [SAMPLE_TASKS[2]] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-ready-specs')).toBeInTheDocument()
    })
    const readySpecsCol = screen.getByTestId('kanban-column-ready-specs')
    const readyTasksCol = screen.getByTestId('kanban-column-ready-tasks')
    expect(within(readySpecsCol).getByText('Build auth module')).toBeInTheDocument()
    expect(within(readyTasksCol).getByText('Fix header bug')).toBeInTheDocument()
  })

  it('task with status open shows Ready as pill label', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [{ id: 'to1', title: 'Open task', status: 'open', spec_id: null }] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Open task')).toBeInTheDocument()
    })
    expect(screen.getByTestId('card-status-pill')).toHaveTextContent('Ready')
  })

  it('task with status in_progress shows In progress as pill label', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [{ id: 'tip1', title: 'Active task', status: 'in_progress', spec_id: null }] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Active task')).toBeInTheDocument()
    })
    expect(screen.getByTestId('card-status-pill')).toHaveTextContent('In progress')
  })
})

describe('Backlog needle ID chips (→1488)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('task card renders task-id-chip showing the task ID', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [] })
      if (url === '/tasks') return Promise.resolve({ tasks: [{ id: 'to1', title: 'Some task', status: 'open', spec_id: null }] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Some task')).toBeInTheDocument()
    })
    const chip = screen.getByTestId('task-id-chip')
    expect(chip).toBeInTheDocument()
    expect(chip).toHaveTextContent('→to1')
  })

  it('spec card renders spec-id-chip showing the slug derived from path', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [{ id: 's1', title: 'Auth spec', status: 'draft', task_ids: [], path: 'docs/spec/auth.md' }] })
      if (url === '/tasks') return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Auth spec')).toBeInTheDocument()
    })
    const chip = screen.getByTestId('spec-id-chip')
    expect(chip).toBeInTheDocument()
    expect(chip).toHaveTextContent('auth')
  })
})

describe('Backlog →1501 — cards link to detail pages', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [{ id: 's1', title: 'Auth spec', status: 'draft', task_ids: [], path: 'docs/spec/auth.md' }] })
      if (url === '/tasks') return Promise.resolve({ tasks: [{ id: 't1', title: 'Write tests', status: 'open', spec_id: null }] })
      return Promise.resolve({})
    })
  })

  it('TaskCard wraps card in a Link to /tasks?focus={id}', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Write tests')).toBeInTheDocument()
    })
    const link = screen.getByTestId('task-card-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/tasks?focus=t1')
  })

  it('SpecCard wraps card in a Link to /specs?focus={path}', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText('Auth spec')).toBeInTheDocument()
    })
    const link = screen.getByTestId('spec-card-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/specs?focus=docs%2Fspec%2Fauth.md')
  })
})
