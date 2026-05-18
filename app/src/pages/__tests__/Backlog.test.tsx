import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Backlog from '../Backlog'

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../../lib/spawn', () => ({
  buildSpec: vi.fn(),
}))

// Simulate the real behavior: Specs renders a fixed TopBar when NOT embedded.
// After the fix, Backlog passes embedded=true so the banner never renders.
vi.mock('../Specs', () => ({
  default: ({ embedded }: { embedded?: boolean }) => (
    <div data-testid="specs-content">
      {!embedded && <header data-testid="specs-topbar">Specs TopBar</header>}
    </div>
  ),
}))

vi.mock('../Tasks', () => ({
  default: ({ embedded }: { embedded?: boolean }) => (
    <div data-testid="tasks-content">
      {!embedded && <header data-testid="tasks-topbar">Tasks TopBar</header>}
    </div>
  ),
}))

vi.mock('../../components/ConflictDialog', () => ({
  default: () => null,
}))

function renderBacklogAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/backlog" element={<Backlog />} />
        <Route path="/backlog/specs" element={<Backlog />} />
        <Route path="/backlog/tasks" element={<Backlog />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Backlog sub-tab nav', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows All/Specs/Tasks nav on the All tab', () => {
    renderBacklogAt('/backlog')
    expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Specs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
  })

  it('keeps All/Specs/Tasks nav visible when Specs tab is active', () => {
    renderBacklogAt('/backlog/specs')
    expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Specs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
  })

  it('keeps All/Specs/Tasks nav visible when Tasks tab is active', () => {
    renderBacklogAt('/backlog/tasks')
    expect(screen.getByRole('link', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Specs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
  })

  // RED: before fix, Specs renders its own fixed TopBar (role=banner) when
  // embedded inside Backlog, covering the nav row. After fix, embedded=true
  // suppresses the TopBar so no banner element appears.
  it('does not render a fixed TopBar inside Backlog when Specs tab is active', () => {
    renderBacklogAt('/backlog/specs')
    expect(screen.queryByRole('banner')).not.toBeInTheDocument()
  })

  it('does not render a fixed TopBar inside Backlog when Tasks tab is active', () => {
    renderBacklogAt('/backlog/tasks')
    expect(screen.queryByRole('banner')).not.toBeInTheDocument()
  })
})
