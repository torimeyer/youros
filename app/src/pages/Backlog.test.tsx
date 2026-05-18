import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
      expect(screen.getByText(/plans in flight/i)).toBeInTheDocument()
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

  it('does NOT show spec-linked tasks in the standalone section with real shapes', async () => {
    renderBacklog('/backlog')
    await waitFor(() => {
      expect(screen.getByText(/standalone tasks/i)).toBeInTheDocument()
    })
    expect(screen.queryByText('Write tests')).toBeNull()
    expect(screen.queryByText('Implement login')).toBeNull()
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

  it('shows "Plans in flight" section heading on the All tab', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText(/plans in flight/i)).toBeInTheDocument()
    })
  })

  it('shows "Standalone tasks" section heading on the All tab', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText(/standalone tasks/i)).toBeInTheDocument()
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

  it('does NOT list spec-linked tasks in the standalone section', async () => {
    renderBacklog()
    await waitFor(() => {
      expect(screen.getByText(/standalone tasks/i)).toBeInTheDocument()
    })
    expect(screen.queryByText('Write tests')).toBeNull()
    expect(screen.queryByText('Implement login')).toBeNull()
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
})
