import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import TasksAuditModal from './TasksAuditModal'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiPost = vi.mocked(api.post)
const mockedApiGet = vi.mocked(api.get)

beforeEach(() => {
  vi.clearAllMocks()
  // Minimal responses so the modal renders without errors.
  mockedApiPost.mockResolvedValue({ job_id: 'job-1' } as never)
  mockedApiGet.mockResolvedValue({
    status: 'running',
    checked: 0,
    total: 0,
    results: { closed: [], review: [], skipped_irl: 0, errors: [] },
  } as never)
})

describe('TasksAuditModal', () => {
  it('does not render when open is false', () => {
    render(
      <TasksAuditModal open={false} onClose={vi.fn()} />
    )
    expect(screen.queryByTestId('tasks-audit-modal')).not.toBeInTheDocument()
  })

  it('calls onClose when Escape is pressed', async () => {
    const onClose = vi.fn()
    render(
      <TasksAuditModal open={true} onClose={onClose} />
    )

    // Wait for the modal to render.
    await waitFor(() => {
      expect(screen.getByTestId('tasks-audit-modal')).toBeInTheDocument()
    })

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
