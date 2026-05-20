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


vi.mock('./Specs', () => ({
  default: () => <div data-testid="specs-page">Specs page</div>,
}))

vi.mock('./Tasks', () => ({
  default: () => <div data-testid="tasks-page">Tasks page</div>,
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
      expect(screen.getByTestId('kanban-column-ready')).toBeInTheDocument()
    })
    // All open tasks appear in Ready column (Patterson step 3)
    expect(screen.getByText('Write tests')).toBeInTheDocument()
    expect(screen.getByText('Implement login')).toBeInTheDocument()
  })
})

describe('Backlog page (→1466)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: SAMPLE_SPECS })
      if (url === '/tasks') return Promise.resolve({ tasks: SAMPLE_TASKS })
      return Promise.resolve({})
    })
  })

  it('renders three sub-tab links: All, Specs, Tasks', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Specs' })).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
    })
  })

  it('All tab shows the All view by default at /backlog — not Specs or Tasks sub-pages', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
    })
    expect(screen.queryByTestId('specs-page')).toBeNull()
    expect(screen.queryByTestId('tasks-page')).toBeNull()
  })

  it('shows kanban Drafting column on the All tab', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-drafting')).toBeInTheDocument()
    })
  })

  it('shows kanban In progress column on the All tab', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-in-progress')).toBeInTheDocument()
    })
  })

  it('renders spec titles in Plans in flight section', async () => {
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

  it('renders standalone tasks (those not linked to any spec)', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText('Fix header bug')).toBeInTheDocument()
      expect(screen.getByText('Update README')).toBeInTheDocument()
    })
  })

  it('shows all open tasks in the kanban ready column (including spec-linked)', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByTestId('kanban-column-ready')).toBeInTheDocument()
    })
    // All open tasks appear in Ready column (Patterson step 3); spec-linked tasks included
    expect(screen.getByText('Write tests')).toBeInTheDocument()
    expect(screen.getByText('Implement login')).toBeInTheDocument()
  })

  it('renders the Specs sub-page at /backlog/specs', async () => {
    renderBacklog('/backlog/specs')
    await waitFor(() => {
      expect(screen.getByTestId('specs-page')).toBeInTheDocument()
    })
  })

  it('renders the Tasks sub-page at /backlog/tasks', async () => {
    renderBacklog('/backlog/tasks')
    await waitFor(() => {
      expect(screen.getByTestId('tasks-page')).toBeInTheDocument()
    })
  })

  it('clicking the Specs tab navigates to /backlog/specs', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Specs' })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('link', { name: 'Specs' }))
    await waitFor(() => {
      expect(screen.getByTestId('specs-page')).toBeInTheDocument()
    })
  })

  it('clicking the Tasks tab navigates to /backlog/tasks', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('link', { name: 'Tasks' }))
    await waitFor(() => {
      expect(screen.getByTestId('tasks-page')).toBeInTheDocument()
    })
  })

  it('does not render a Spec Health tab link (→1479)', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
    })
    expect(screen.queryByRole('link', { name: 'Spec Health' })).toBeNull()
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
      expect(screen.getByTestId('kanban-column-drafting')).toBeInTheDocument()
    })
    const draftingCol = screen.getByTestId('kanban-column-drafting')
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
      expect(screen.getByTestId('kanban-column-ready')).toBeInTheDocument()
    })
    const readyCol = screen.getByTestId('kanban-column-ready')
    expect(within(readyCol).getByText('Build auth module')).toBeInTheDocument()
    expect(within(readyCol).getByText('Fix header bug')).toBeInTheDocument()
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
