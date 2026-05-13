import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ReleaseNotesWatcher from './ReleaseNotesWatcher'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'

vi.mock('../lib/sidebarBus', () => ({
  onSpecsChange: (_: () => void) => () => {},
}))

function specsResponse(
  specs: Array<{ path: string; title?: string; status?: string; ac?: string[]; updated_at_ms?: number }>
) {
  return {
    docs: specs.map((s) => ({
      path: s.path,
      title: s.title || s.path,
      status: s.status || 'draft',
      updated_at_ms: s.updated_at_ms,
      acceptance_criteria: (s.ac || []).map((text) => ({ text, checked: true })),
    })),
  }
}

function fetchOk(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body })
}

function seedStore(onboarded = true) {
  useAppStore.setState({
    onboarded,
    hydrated: true,
    chatOpen: false,
    setChatOpen: (v: boolean) => useAppStore.setState({ chatOpen: v }),
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation(() => fetchOk({ docs: [] }))
  vi.stubGlobal('fetch', fetchMock)
  window.localStorage.clear()
  seedStore(true)
  useNotificationStore.getState().clearAll()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('ReleaseNotesWatcher', () => {
  it('does NOT celebrate a spec that is already complete on mount (seed-on-mount)', async () => {
    fetchMock.mockImplementation(() =>
      fetchOk(specsResponse([{
        path: 'docs/spec/multi-model.md',
        title: 'Multi model side by side chat',
        status: 'complete',
        ac: ['Chat input has a Both toggle', 'Claude on the left', 'Gemini on the right'],
      }]))
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    const saved = window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')
    expect(saved).not.toBeNull()
    expect(JSON.parse(saved as string)).toContain('docs/spec/multi-model.md')
  })

  it('celebrates a true in-progress -> complete transition', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fetchMock
      .mockImplementationOnce(() => fetchOk(specsResponse([{ path: 'docs/spec/x.md', title: 'X', status: 'in-progress' }])))
      .mockImplementation(() => fetchOk(specsResponse([{ path: 'docs/spec/x.md', title: 'X', status: 'complete' }])))

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    await vi.advanceTimersByTimeAsync(2100)

    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
  })

  it('does NOT re-celebrate a spec already in the localStorage dedup set', async () => {
    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/seen.md'])
    )
    fetchMock.mockImplementation(() =>
      fetchOk(specsResponse([{ path: 'docs/spec/seen.md', title: 'Seen', status: 'complete' }]))
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('clears the localStorage dedup when onboarded flips to false (demo reset)', async () => {
    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/seen.md'])
    )
    seedStore(false)

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')).toBeNull()
    })
  })

  it('celebrates from a persistent spec_complete notification when /api/specs is empty', async () => {
    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-fl-42',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Multi model chat is built and ready to try.',
        action_url: '/specs?expand=docs/spec/multi-model.md',
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
    expect(screen.getByText('Your feature is live')).toBeInTheDocument()
    expect(
      screen.getByText('Multi model chat is built and ready to try.')
    ).toBeInTheDocument()
  })

  it('celebrates a spec that completed within the recent grace window even on first poll (hard refresh case)', async () => {
    const recentMs = Date.now() - 5_000
    fetchMock.mockImplementation(() =>
      fetchOk({
        docs: [{
          path: 'docs/spec/calendar.md',
          title: 'Calendar sync',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [{ text: 'Events appear', checked: true }],
        }],
      })
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
    expect(screen.getByText('Calendar sync')).toBeInTheDocument()
  })

  it('stays silent for a spec that completed long ago (outside grace window)', async () => {
    const staleMs = Date.now() - 10 * 60 * 1000
    fetchMock.mockImplementation(() =>
      fetchOk({
        docs: [{
          path: 'docs/spec/stale.md',
          title: 'Stale spec',
          status: 'complete',
          updated_at_ms: staleMs,
        }],
      })
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire the modal for the same spec_complete notification twice', async () => {
    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-dedup',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Body',
        action_url: '/specs',
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    unmount()
    render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire when notification arrives for a spec already celebrated via spec polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    fetchMock
      .mockImplementationOnce(() =>
        fetchOk(specsResponse([{ path: 'docs/spec/auto-close.md', title: 'auto close', status: 'in-progress' }]))
      )
      .mockImplementation(() =>
        fetchOk(specsResponse([{ path: 'docs/spec/auto-close.md', title: 'auto close', status: 'complete' }]))
      )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => { expect(fetchMock).toHaveBeenCalled() })
    await vi.advanceTimersByTimeAsync(2100)

    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    })

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-double-fire',
        type: 'spec_complete',
        title: 'auto close',
        body: 'auto close is built and ready to try.',
        action_url: '/specs?expand=docs/spec/auto-close.md',
      })
    })

    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('notification path seeds the spec path into celebratedRef so a 60s-window refresh does not re-fire', async () => {
    const recentMs = Date.now() - 5_000

    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-bridge',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'auto close is built and ready to try.',
        action_url: '/specs?expand=docs/spec/auto-close.md',
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    unmount()

    fetchMock.mockImplementation(() =>
      fetchOk({
        docs: [{
          path: 'docs/spec/auto-close.md',
          title: 'auto close',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [],
        }],
      })
    )

    render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire a previously-acknowledged notification after onboarded flips to false', async () => {
    seedStore(true)

    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-persist-reset',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'auto close is built and ready to try.',
        action_url: '/specs?expand=docs/spec/auto-close.md',
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    unmount()
    seedStore(false)

    render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does NOT re-show an acknowledged spec in the same session after onboarded resets to false', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const recentMs = Date.now() - 5_000

    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/auto-close.md'])
    )
    seedStore(true)

    fetchMock.mockImplementation(() =>
      fetchOk({
        docs: [{
          path: 'docs/spec/auto-close.md',
          title: 'auto close',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [],
        }],
      })
    )

    render(<ReleaseNotesWatcher />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()

    act(() => seedStore(false))

    await vi.advanceTimersByTimeAsync(2200)

    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')).toBeNull()
  })

  it('closes the release-notes modal when Escape is pressed', async () => {
    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-esc',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Escape should dismiss me.',
        action_url: '/specs',
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    })
  })

  // ── canonical polling-pattern compliance ──────────────────────────

  it('aborts in-flight fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts?.signal as AbortSignal
      return new Promise(() => {}) // never resolves
    })

    const { unmount } = render(<ReleaseNotesWatcher />)
    // Let the initial tick start
    await act(async () => {})

    expect(capturedSignal).toBeDefined()
    expect(capturedSignal?.aborted).toBe(false)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('doubles backoff after fetch errors, up to 30s', async () => {
    vi.useFakeTimers()
    fetchMock.mockRejectedValue(new Error('network error'))

    render(<ReleaseNotesWatcher />)

    // First tick fires immediately, fails → backoff = 1000
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Next tick at 1000ms
    await act(async () => { vi.advanceTimersByTime(1000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Next tick at 2000ms (doubled)
    await act(async () => { vi.advanceTimersByTime(2000) })
    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('does not pile up requests when fetch is slow (chained setTimeout, not setInterval)', async () => {
    vi.useFakeTimers()

    // Fetch never resolves — simulates a very slow server
    fetchMock.mockImplementation(() => new Promise(() => {}))

    render(<ReleaseNotesWatcher />)

    // Advance well past multiple 2s intervals
    await act(async () => { vi.advanceTimersByTime(10_000) })
    await act(async () => {})

    // Only 1 in-flight request — no pile-up
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
