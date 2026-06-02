import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ReleaseNotesWatcher from './ReleaseNotesWatcher'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('../lib/sidebarBus', () => ({
  onSpecsChange: (_: () => void) => () => {},
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

function specsResponse(
  specs: Array<{ path: string; title?: string; status?: string; ac?: string[] }>
) {
  return {
    docs: specs.map((s) => ({
      path: s.path,
      title: s.title || s.path,
      status: s.status || 'draft',
      acceptance_criteria: (s.ac || []).map((text) => ({ text, checked: true })),
    })),
  }
}

function seedStore(onboarded = true) {
  useAppStore.setState({
    onboarded,
    hydrated: true,
    chatOpen: false,
    // Just enough to satisfy the selectors the component uses.
    setChatOpen: (v: boolean) => useAppStore.setState({ chatOpen: v }),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  seedStore(true)
  // Reset the notifications store so a previous test's lastFeatureLive
  // stamp does not leak into this one (the store is a module-level
  // singleton via zustand's create()).
  useNotificationStore.getState().clearAll()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ReleaseNotesWatcher', () => {
  it('does NOT celebrate a spec that is already complete on mount (seed-on-mount)', async () => {
    // A spec that was already complete before this session started is
    // not news to the user — celebrating it on the first poll meant
    // any demo reset that cleared the localStorage dedup set ended up
    // re-celebrating old, stale specs on the next page load (the exact
    // bug Tori hit on 2026-04-22: the "Multi model side by side chat"
    // modal fired on login before onboarding even finished). The fix
    // seeds the dedup set silently on mount so only true
    // in-session transitions fire a modal afterwards. The
    // persistent spec_complete notification path remains as the
    // surface for fresh completions.
    mockedApiGet.mockResolvedValue(
      specsResponse([
        {
          path: 'docs/spec/multi-model.md',
          title: 'Multi model side by side chat',
          status: 'complete',
          ac: ['Chat input has a Both toggle', 'Claude on the left', 'Gemini on the right'],
        },
      ])
    )

    render(<ReleaseNotesWatcher />)

    // Let the initial poll land.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    // No modal — the complete spec was seeded into the dedup set
    // silently.
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    // And the seed is persisted so a remount inside the same
    // localStorage-era also stays silent.
    const saved = window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')
    expect(saved).not.toBeNull()
    expect(JSON.parse(saved as string)).toContain('docs/spec/multi-model.md')
  })

  it('celebrates a true in-progress -> complete transition', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    // First poll: in-progress. No celebration yet.
    mockedApiGet.mockResolvedValueOnce(
      specsResponse([{ path: 'docs/spec/x.md', title: 'X', status: 'in-progress' }])
    )
    // Second poll: complete. Celebrate.
    mockedApiGet.mockResolvedValueOnce(
      specsResponse([{ path: 'docs/spec/x.md', title: 'X', status: 'complete' }])
    )

    render(<ReleaseNotesWatcher />)

    // Let the first poll land.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    // Advance past the 5s poll interval to trigger the second fetch.
    await vi.advanceTimersByTimeAsync(5100)

    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
  })

  it('does NOT re-celebrate a spec already in the localStorage dedup set', async () => {
    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/seen.md'])
    )
    mockedApiGet.mockResolvedValue(
      specsResponse([{ path: 'docs/spec/seen.md', title: 'Seen', status: 'complete' }])
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('clears the localStorage dedup when onboarded flips to false (demo reset)', async () => {
    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/seen.md'])
    )
    seedStore(false)
    mockedApiGet.mockResolvedValue(specsResponse([]))

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')).toBeNull()
    })
  })

  it('celebrates from a persistent spec_complete notification when /api/specs is empty', async () => {
    // This is the exact bug Tori hit on the 2026-04-21 demo: /api/specs
    // came back with docs: [] because the spec file had been cleaned
    // up after the build finished. The watcher's old path (poll /specs
    // for a transition) had nothing to detect, so the modal never
    // fired. The fix wires a second path: the TopBar poll picks up
    // the persistent spec_complete notification, stamps
    // lastFeatureLive on the notifications store, and this watcher
    // uses that stamp to open the release-notes modal even when the
    // spec itself is gone.
    mockedApiGet.mockResolvedValue(specsResponse([]))

    render(<ReleaseNotesWatcher />)

    // Let the initial /specs poll land and confirm the modal is NOT
    // open yet (empty docs list has nothing to celebrate).
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()

    // Now simulate the TopBar poll handing a spec_complete persistent
    // notification to the store.
    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-fl-42',
        type: 'spec_complete',
        title: 'Your feature is live',
        body: 'Multi model chat is built and ready to try.',
        action_url: '/specs?expand=docs/spec/multi-model.md',
      })
    })

    // The watcher must see the new lastFeatureLive stamp and render
    // the modal with the notification's title and body.
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
    expect(screen.getByText('Your feature is live')).toBeInTheDocument()
    expect(
      screen.getByText('Multi model chat is built and ready to try.')
    ).toBeInTheDocument()
  })

  it('celebrates a spec that completed within the recent grace window even on first poll (hard refresh case)', async () => {
    // Regression for Tori's 2026-04-22 demo: a spec flipped to
    // complete, the user hard-refreshed before the modal fired,
    // and the watcher re-mounted to find the spec already
    // complete. The old seed-on-mount behavior silently added the
    // spec to the dedup set and the modal never fired. The fix:
    // if the spec's file mtime is within a 60s grace window, it
    // is a "just-landed" win and still opens the modal.
    const recentMs = Date.now() - 5_000 // 5 seconds ago, well inside window
    mockedApiGet.mockResolvedValue({
      docs: [
        {
          path: 'docs/spec/calendar.md',
          title: 'Calendar sync',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [{ text: 'Events appear', checked: true }],
        },
      ],
    })

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })
    expect(screen.getByText('Calendar sync')).toBeInTheDocument()
  })

  it('stays silent for a spec that completed long ago (outside grace window)', async () => {
    // Counterpart to the grace-window case: a stale spec from a
    // previous run must NOT fire the modal on mount, even though
    // it is complete.
    const staleMs = Date.now() - 10 * 60 * 1000 // 10 minutes ago
    mockedApiGet.mockResolvedValue({
      docs: [
        {
          path: 'docs/spec/stale.md',
          title: 'Stale spec',
          status: 'complete',
          updated_at_ms: staleMs,
        },
      ],
    })

    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire the modal for the same spec_complete notification twice', async () => {
    // Dedup guard: if the TopBar poll re-delivers the same
    // spec_complete row (or the store re-renders for any reason), the
    // modal must fire at most once per notification id. Without this,
    // a rapid re-poll would spam a modal dialog onto the user.
    mockedApiGet.mockResolvedValue(specsResponse([]))

    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
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

    // Dismiss + re-mount the watcher while the same lastFeatureLive
    // stamp is still on the store. The modal must NOT re-open because
    // the celebrated-paths dedup set now contains the notification id.
    unmount()
    render(<ReleaseNotesWatcher />)
    // Give the new mount a beat to run its effects.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire when notification arrives for a spec already celebrated via spec polling', async () => {
    // Root-cause regression for the double-fire bug. The /api/specs polling
    // path fires at ~2s after spec completion and stores "docs/spec/foo.md"
    // in the celebrated set. The notification path fires ~10s later (TopBar
    // poll) with a different key ("notification:{id}"). Without the fix,
    // both paths opened the modal independently. With specPath sharing the
    // same dedup key, the notification path sees the already-celebrated key
    // and stays silent.
    vi.useFakeTimers({ shouldAdvanceTime: true })

    // First poll: in-progress. Second: complete (observed in-session transition).
    mockedApiGet.mockResolvedValueOnce(
      specsResponse([{ path: 'docs/spec/auto-close.md', title: 'auto close', status: 'in-progress' }])
    )
    mockedApiGet.mockResolvedValue(
      specsResponse([{ path: 'docs/spec/auto-close.md', title: 'auto close', status: 'complete' }])
    )

    render(<ReleaseNotesWatcher />)

    await waitFor(() => { expect(mockedApiGet).toHaveBeenCalled() })
    await vi.advanceTimersByTimeAsync(5100)

    await waitFor(() => {
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    })

    // Dismiss the modal.
    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    })

    // Now the notification path fires for the SAME spec.
    act(() => {
      useNotificationStore.getState().addPersistentToast({
        id: 'notif-double-fire',
        type: 'spec_complete',
        title: 'auto close',
        body: 'auto close is built and ready to try.',
        action_url: '/specs?expand=docs/spec/auto-close.md',
      })
    })

    // The modal must NOT re-open because 'docs/spec/auto-close.md' is
    // already in the celebrated set from the spec polling path.
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('notification path seeds the spec path into celebratedRef so a 60s-window refresh does not re-fire', async () => {
    // Regression for the random "Your feature is live" modal:
    // The notification path keyed celebratedRef on "notification:<id>"
    // while the polling path keyed on "docs/spec/foo.md". After dismissing
    // via the notification path, a hard-refresh within the 60s grace
    // window would re-fire the modal via the polling path because the spec
    // path was absent from the persisted dedup set.
    //
    // Fix: when the notification path fires, specPath is shared via
    // lastFeatureLive.specPath so celebratedRef uses the same key as
    // the /api/specs polling path, blocking the second open.
    const recentMs = Date.now() - 5_000 // 5 seconds ago

    // Simulate the notification path firing first (spec file might be gone).
    mockedApiGet.mockResolvedValue(specsResponse([]))

    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
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

    // User dismisses.
    unmount()

    // Now the spec file appears on the next poll within the grace window.
    // The polling path must NOT re-fire because the spec path was seeded
    // into celebratedRef (and localStorage) when the notification fired.
    mockedApiGet.mockResolvedValue({
      docs: [
        {
          path: 'docs/spec/auto-close.md',
          title: 'auto close',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [],
        },
      ],
    })

    render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does not re-fire a previously-acknowledged notification after onboarded flips to false', async () => {
    // Regression: PATCH /api/settings {onboarded:false} (demo reset) clears
    // the ephemeral spec-path dedup (celebratedRef + localStorage). The old
    // code used seenFeatureLiveIdRef which was in-memory only and reset to
    // null on every page load (component remount). So:
    //   1. User sees "auto close is live" notification → dismisses it
    //   2. Demo reset sets onboarded=false → celebratedRef cleared
    //   3. Page reload → seenFeatureLiveIdRef = null, celebratedRef empty
    //   4. lastFeatureLive still in store → modal re-fires
    //
    // Fix: notification IDs are now stored in myos-permanent-seen-notification-ids
    // which is NOT cleared by the onboarded=false handler.
    mockedApiGet.mockResolvedValue(specsResponse([]))
    seedStore(true)

    const { unmount } = render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
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

    // User dismisses the modal (component unmounts).
    unmount()

    // Demo reset: onboarded flips to false. This clears the ephemeral
    // spec-path dedup but must NOT re-expose the already-acknowledged
    // notification ID.
    seedStore(false)

    // Simulate page reload: fresh mount, seenNotificationIdsRef loads
    // from the permanent localStorage key.
    render(<ReleaseNotesWatcher />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    // The modal must NOT appear — the permanent dedup key still has
    // 'notif-persist-reset' even though the ephemeral set was cleared.
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
  })

  it('does NOT re-show an acknowledged spec in the same session after onboarded resets to false', async () => {
    // Regression: when onboarded flips to false the old code wiped celebratedRef
    // in-memory, so the 2s poller immediately re-fired the modal for a recently-
    // completed spec. The fix only clears localStorage (for future sessions) and
    // leaves the in-memory dedup set intact.
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const recentMs = Date.now() - 5_000 // inside the 60s grace window

    // Spec previously acknowledged — in localStorage.
    window.localStorage.setItem(
      'myos-ephemeral-celebrated-spec-paths',
      JSON.stringify(['docs/spec/auto-close.md'])
    )
    seedStore(true)

    mockedApiGet.mockResolvedValue({
      docs: [
        {
          path: 'docs/spec/auto-close.md',
          title: 'auto close',
          status: 'complete',
          updated_at_ms: recentMs,
          acceptance_criteria: [],
        },
      ],
    })

    render(<ReleaseNotesWatcher />)
    // Initial poll: spec already in dedup set — no modal.
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalled())
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()

    // Simulate demo reset: flip onboarded to false.
    act(() => seedStore(false))

    // Advance past the 5s poll to trigger the next fetch.
    await vi.advanceTimersByTimeAsync(5200)

    // Modal must NOT re-appear. The in-memory celebratedRef was preserved.
    expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()

    // localStorage is cleared (future hard-refresh starts clean).
    expect(window.localStorage.getItem('myos-ephemeral-celebrated-spec-paths')).toBeNull()
  })

  it('shows checked criteria with a green check icon and unchecked criteria with a hollow icon', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockedApiGet.mockResolvedValueOnce(
      specsResponse([{ path: 'docs/spec/mixed.md', title: 'Mixed', status: 'in-progress' }])
    )
    mockedApiGet.mockResolvedValueOnce({
      docs: [{
        path: 'docs/spec/mixed.md',
        title: 'Mixed',
        status: 'complete',
        acceptance_criteria: [
          { text: 'SC-001: The widget loads', checked: true },
          { text: 'SC-002: Errors are handled', checked: false },
        ],
      }],
    })
    render(<ReleaseNotesWatcher />)
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(5100)
    await waitFor(() =>
      expect(screen.getByTestId('release-notes-modal')).toBeInTheDocument()
    )
    const checkedItem = screen.getByTestId('ac-item-checked')
    expect(checkedItem).toBeInTheDocument()
    const uncheckedItem = screen.getByTestId('ac-item-unchecked')
    expect(uncheckedItem).toBeInTheDocument()
  })

  it('closes the release-notes modal when Escape is pressed', async () => {
    // Muscle-memory UX: users hit Esc to dismiss any modal.
    mockedApiGet.mockResolvedValue(specsResponse([]))
    render(<ReleaseNotesWatcher />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    // Open the modal via the lastFeatureLive path.
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

    // Press Escape and confirm the modal goes away.
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('release-notes-modal')).not.toBeInTheDocument()
    })
  })
})
