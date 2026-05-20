import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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

vi.mock('../../components/ConflictDialog', () => ({
  default: () => null,
}))

function renderBacklogAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Backlog />
    </MemoryRouter>
  )
}

describe('Backlog (Kanban view) — sub-tabs removed (→1489)', () => {
  it('renders AllView at /backlog with no sub-tab links', async () => {
    renderBacklogAt('/backlog')
    await waitFor(() => {
      expect(screen.getByTestId('backlog-allview')).toBeInTheDocument()
    })
    expect(screen.queryByRole('link', { name: 'All' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Specs' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Tasks' })).toBeNull()
  })
})
