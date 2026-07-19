import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import ShareView from './ShareView'

// ShareView is a public page with no api wrapper: it talks to fetch()
// directly, so the tests stub the global.
const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

function renderShare(token = 'tok-1') {
  return render(
    <MemoryRouter initialEntries={[`/share/${token}`]}>
      <Routes>
        <Route path="/share/:token" element={<ShareView />} />
      </Routes>
    </MemoryRouter>
  )
}

const baseShare = {
  token: 'tok-1',
  title: 'Sprint cleanup',
  created_at: '2026-07-18T12:00:00Z',
  expires_at: '2026-07-25T12:00:00Z',
}

const taskListShare = {
  ...baseShare,
  share_type: 'task_list',
  content_snapshot: [
    {
      id: 't1',
      title: 'Fix the login page',
      priority: 'P1',
      status: 'open',
      created_at: '2026-07-17T09:00:00Z',
      description: 'Users get stuck after signing in.',
    },
    {
      id: 't2',
      title: 'Ship the weekly report',
      priority: 'P0',
      status: 'closed',
      created_at: '2026-07-16T09:00:00Z',
    },
  ],
}

describe('ShareView page', () => {
  it('shows the loading state while the share is being fetched', () => {
    fetchMock.mockImplementation(() => new Promise(() => {}))
    renderShare()
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('requests the share for the token in the URL', async () => {
    fetchMock.mockResolvedValue(jsonResponse(taskListShare))
    renderShare('tok-xyz')
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/shares/tok-xyz')
    })
  })

  it('shows "Link not found" for a 404', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 404))
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Link not found.')).toBeInTheDocument()
    })
    expect(
      screen.getByText('This share link does not exist or has been revoked.')
    ).toBeInTheDocument()
  })

  it('shows the expiry explanation for a 410', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 410))
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('This link has expired.')).toBeInTheDocument()
    })
    expect(screen.getByText('Share links are only valid for 7 days.')).toBeInTheDocument()
  })

  it('falls back to "Link not found" on an unexpected server error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 500))
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Link not found.')).toBeInTheDocument()
    })
  })

  it('falls back to "Link not found" when the network request fails', async () => {
    fetchMock.mockRejectedValue(new Error('network down'))
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Link not found.')).toBeInTheDocument()
    })
  })

  it('renders a task list share: header, count, rows, and expiry date', async () => {
    fetchMock.mockResolvedValue(jsonResponse(taskListShare))
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Sprint cleanup')).toBeInTheDocument()
    })
    expect(screen.getByText('Task list')).toBeInTheDocument()
    expect(screen.getByText('2 tasks')).toBeInTheDocument()
    expect(screen.getByText('Fix the login page')).toBeInTheDocument()
    expect(screen.getByText('Ship the weekly report')).toBeInTheDocument()
    expect(screen.getByText('Users get stuck after signing in.')).toBeInTheDocument()
    // Priority badges and status pills
    expect(screen.getByText('P1')).toBeInTheDocument()
    expect(screen.getByText('P0')).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
    expect(screen.getByText('closed')).toBeInTheDocument()
    // A finished task reads as done (struck through)
    expect(screen.getByText('Ship the weekly report').className).toContain('line-through')
    expect(screen.getByText('Fix the login page').className).not.toContain('line-through')
    // The expiry date is rendered with the same locale rules the page uses
    const expectedExpiry = new Date(taskListShare.expires_at).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
    expect(screen.getByText(new RegExp(`Expires ${expectedExpiry}`))).toBeInTheDocument()
  })

  it('uses the singular form for a one-task share', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...taskListShare,
        content_snapshot: [taskListShare.content_snapshot[0]],
      })
    )
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('1 task')).toBeInTheDocument()
    })
  })

  it('renders an agent output share with the agent name and its output', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...baseShare,
        share_type: 'agent_output',
        title: 'Overnight fix run',
        content_snapshot: [
          { agent: 'saa-night-fixer', output: 'All 12 tests pass after the fix.' },
        ],
      })
    )
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Agent output')).toBeInTheDocument()
    })
    expect(screen.getByText('saa-night-fixer')).toBeInTheDocument()
    expect(screen.getByText('All 12 tests pass after the fix.')).toBeInTheDocument()
  })

  it('renders a file share with provenance attribution and the file content', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...baseShare,
        share_type: 'file',
        title: 'notes.md',
        content_snapshot: [
          {
            file_name: 'notes.md',
            file_path: '/tmp/notes.md',
            content: 'Meeting notes: ship on Friday.',
            is_binary: false,
            provenance: { agent_name: 'saa-note-taker' },
          },
        ],
      })
    )
    renderShare()
    await waitFor(() => {
      expect(screen.getByTestId('file-share-attribution')).toBeInTheDocument()
    })
    expect(screen.getByText('Shared by yourOS')).toBeInTheDocument()
    expect(screen.getByText('saa-note-taker')).toBeInTheDocument()
    expect(screen.getByText('Meeting notes: ship on Friday.')).toBeInTheDocument()
    expect(screen.getByText('File')).toBeInTheDocument()
  })

  it('shows a placeholder instead of content for a binary file', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...baseShare,
        share_type: 'file',
        title: 'photo.png',
        content_snapshot: [
          {
            file_name: 'photo.png',
            file_path: '/tmp/photo.png',
            content: '',
            is_binary: true,
            provenance: null,
          },
        ],
      })
    )
    renderShare()
    await waitFor(() => {
      expect(screen.getByText(/Binary file/)).toBeInTheDocument()
    })
  })

  it('shows "Nothing to show here." for an empty snapshot', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...baseShare,
        share_type: 'task_list',
        content_snapshot: [],
      })
    )
    renderShare()
    await waitFor(() => {
      expect(screen.getByText('Nothing to show here.')).toBeInTheDocument()
    })
  })
})
