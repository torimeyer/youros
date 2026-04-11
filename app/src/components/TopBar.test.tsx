import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import TopBar from './TopBar'
import { useNotificationStore } from '../stores/notifications'
import { useAppStore } from '../stores/app'

// Mock the api module so the persistent notifications endpoint
// does not hit the network. Tests can override these per case.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

function renderTopBar() {
  return render(
    <BrowserRouter>
      <TopBar title="Home" />
    </BrowserRouter>
  )
}

// Helper that seeds the notification store with `count` unread items.
function seedUnreadNotifications(count: number) {
  const items = Array.from({ length: count }, (_, i) => ({
    id: `notif-${i}`,
    agentName: `agent-${i}`,
    prevStatus: 'spawned',
    status: 'completed',
    timestamp: new Date().toISOString(),
    read: false,
  }))
  useNotificationStore.setState({ notifications: items, toastIds: [] })
}

// Build a persistent notification fixture.
function makePersistent(id: string, read = false) {
  return {
    id,
    type: 'agent',
    title: `Item ${id}`,
    body: `body ${id}`,
    action_label: null,
    action_url: null,
    read,
    created_at: new Date().toISOString(),
    metadata: {},
  }
}

// Find the notifications bell button by walking up from its icon span,
// which is more reliable than matching the badge number text since
// other components on the bar may render similar text.
function getBellButton(): HTMLButtonElement {
  const icons = Array.from(
    document.querySelectorAll('span.material-symbols-outlined')
  )
  const bellIcon = icons.find((el) => el.textContent === 'notifications')
  if (!bellIcon) throw new Error('Could not find notifications bell icon')
  const button = bellIcon.closest('button')
  if (!button) throw new Error('Bell icon is not inside a button')
  return button as HTMLButtonElement
}

// Read the current badge text off the bell button. Returns null when the
// badge is not rendered (unreadCount is 0).
function getBadgeText(): string | null {
  const bell = getBellButton()
  const badge = bell.querySelector('span.bg-blue-500')
  if (!badge) return null
  return badge.textContent
}

// Parse the badge as a number. "9+" becomes 10 so comparisons still work
// when the count overflows. Returns 0 when the badge is not present.
function getBadgeCount(): number {
  const text = getBadgeText()
  if (text === null) return 0
  if (text === '9+') return 10
  return parseInt(text, 10)
}

// Count the rendered list items inside the dropdown body.
// The dropdown body uses a flex container with hover classes. We locate
// items by their top-level class signature. When the empty state is
// shown, this returns 0.
function getRenderedItemCount(): number {
  // Empty state shows the notifications_none icon inside a p-6 wrapper.
  const emptyState = document.querySelector('.p-6.text-center')
  if (emptyState) return 0
  const listContainer = document.querySelector('.max-h-80.overflow-y-auto')
  if (!listContainer) return 0
  return listContainer.children.length
}

// Count the rendered UNREAD list items inside the dropdown body.
// Read items are rendered with the opacity-60 class to dim them, so
// we exclude those from the count. The badge should match this number
// exactly on every render.
function getRenderedUnreadCount(): number {
  const emptyState = document.querySelector('.p-6.text-center')
  if (emptyState) return 0
  const listContainer = document.querySelector('.max-h-80.overflow-y-auto')
  if (!listContainer) return 0
  let unread = 0
  for (const child of Array.from(listContainer.children)) {
    if (!child.className.includes('opacity-60')) unread += 1
  }
  return unread
}

// The core invariant this whole file is defending:
// the badge on the bell must always equal the number of UNREAD items
// the dropdown would render. If this ever fails, Tori sees "9+" on the
// bell and "You're all caught up" in the dropdown.
function assertBadgeMatchesDropdown(context: string) {
  const badge = getBadgeCount()
  // When the drawer is closed we still assert the dropdown body, if
  // rendered, would match. We open via state inspection instead of
  // a click here.
  const drawerOpen = !!screen.queryByText('Notifications')
  if (!drawerOpen) {
    // Drawer is closed. The invariant is tested once the dropdown opens.
    return
  }
  const unreadVisible = getRenderedUnreadCount()
  expect(
    badge,
    `${context}: badge=${badge} but dropdown shows ${unreadVisible} unread items`
  ).toBe(unreadVisible)

  // Extra guard: if the badge is > 0, the dropdown must NOT show the
  // empty state. This is the exact bug from the screenshot.
  if (badge > 0) {
    expect(
      screen.queryByText(/You.*all caught up/i),
      `${context}: badge is ${badge} but dropdown shows empty state`
    ).not.toBeInTheDocument()
  }
}

describe('TopBar badge and dropdown invariant', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications') return []
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('Case A: only agent store has unread, badge equals dropdown unread count', async () => {
    seedUnreadNotifications(3)
    // Persistent endpoint is empty.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications') return []
      return {}
    })

    renderTopBar()

    // Wait for the mount effect fetch to settle.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    // Badge should be 3 before opening.
    expect(getBadgeCount()).toBe(3)

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    assertBadgeMatchesDropdown('Case A agent-only')
    expect(getRenderedUnreadCount()).toBe(3)
  })

  it('Case B: only persistent endpoint returns items, badge equals dropdown unread count', async () => {
    // Server returns 2 persistent unread items and a stale count of 9.
    // Before the fix, the badge would read from the stale count and
    // show 9 while the dropdown only has 2 items.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 9 }
      if (url === '/notifications')
        return [makePersistent('p-1'), makePersistent('p-2')]
      return {}
    })

    renderTopBar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    // Badge must reflect the 2 actual list items, NOT the stale count.
    await waitFor(() => {
      expect(getBadgeCount()).toBe(2)
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    assertBadgeMatchesDropdown('Case B persistent-only')
    expect(getRenderedUnreadCount()).toBe(2)
  })

  it('Case C: screenshot case, count endpoint says 9 but list is empty, badge must be 0', async () => {
    // This is the exact bug Tori screenshotted. The /unread/count
    // endpoint returns a stale 9 while /notifications returns []. The
    // badge must NOT display 9+ when there is nothing to show.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 9 }
      if (url === '/notifications') return []
      return {}
    })

    renderTopBar()

    // Wait for the proactive list fetch to land.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    // Badge must be 0, not 9 and not "9+".
    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })
    expect(getBadgeText()).toBeNull()

    // Open the drawer and confirm the empty state is consistent.
    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    expect(screen.getByText(/You.*all caught up/i)).toBeInTheDocument()
    assertBadgeMatchesDropdown('Case C stale count, empty list')
  })

  it('Mark all read zeroes the badge and shows the empty state branch for unread', async () => {
    // Seed both sources with unread items.
    seedUnreadNotifications(2)
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 1 }
      if (url === '/notifications') return [makePersistent('p-1')]
      return {}
    })

    renderTopBar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    // Badge is 3 before opening (2 agent + 1 persistent).
    await waitFor(() => {
      expect(getBadgeCount()).toBe(3)
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    const markAllButton = await screen.findByText('Mark all read')
    assertBadgeMatchesDropdown('before mark all read')

    await act(async () => {
      fireEvent.click(markAllButton)
    })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/notifications/read-all')
    })

    // Badge must now be 0 since everything is flushed.
    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })

    // Dropdown is still open (mark all read does not close it) but the
    // items are now rendered with opacity-60, so unread count is 0.
    expect(screen.getByText('Notifications')).toBeInTheDocument()
    assertBadgeMatchesDropdown('after mark all read')
  })

  it('opening the drawer does not change the badge count', async () => {
    seedUnreadNotifications(4)
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications') return []
      return {}
    })

    renderTopBar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    const before = getBadgeCount()
    expect(before).toBe(4)

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // Badge must not have changed just from opening.
    expect(getBadgeCount()).toBe(before)
    assertBadgeMatchesDropdown('after open, no mark read')
  })

  it('invariant holds when both sources have a mix of read and unread items', async () => {
    // Seed agent store with 2 unread + 1 read.
    useNotificationStore.setState({
      notifications: [
        { id: 'a-1', agentName: 'one', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: false },
        { id: 'a-2', agentName: 'two', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: false },
        { id: 'a-3', agentName: 'three', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: true },
      ],
      toastIds: [],
    })
    // Persistent: 1 unread + 2 read.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 1 }
      if (url === '/notifications')
        return [
          makePersistent('p-1', false),
          makePersistent('p-2', true),
          makePersistent('p-3', true),
        ]
      return {}
    })

    renderTopBar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    // 2 agent unread + 1 persistent unread = 3 badge.
    await waitFor(() => {
      expect(getBadgeCount()).toBe(3)
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // Dropdown renders all 6 items (3 agent + 3 persistent) but only 3
    // are unread. Badge must match unread count.
    expect(getRenderedItemCount()).toBe(6)
    expect(getRenderedUnreadCount()).toBe(3)
    assertBadgeMatchesDropdown('mixed read and unread in both sources')
  })

  it('invariant holds when the server returns zero unread but stale items exist', async () => {
    // All items exist on server but are already marked read. The
    // dropdown renders them dimmed, the badge must show 0.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications')
        return [makePersistent('p-1', true), makePersistent('p-2', true)]
      return {}
    })

    renderTopBar()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // Dropdown shows 2 items but both are read, so unread count is 0.
    expect(getRenderedItemCount()).toBe(2)
    expect(getRenderedUnreadCount()).toBe(0)
    assertBadgeMatchesDropdown('all stale items already read')
  })
})

describe('TopBar notifications drawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset stores so state from a previous test does not leak in.
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
    // Default API responses: no persistent notifications.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications') return []
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('clicking the bell icon opens the drawer without marking anything read', async () => {
    seedUnreadNotifications(3)
    renderTopBar()

    // Drawer is not visible yet.
    expect(screen.queryByText('Notifications')).not.toBeInTheDocument()

    // Click the bell.
    fireEvent.click(getBellButton())

    // Drawer is now visible.
    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // None of the agent notifications were marked read.
    const state = useNotificationStore.getState()
    expect(state.notifications.every((n) => !n.read)).toBe(true)

    // The mark-all-read API was NOT called on open.
    expect(mockedApiPost).not.toHaveBeenCalledWith('/notifications/read-all')
  })

  it('shows the "Mark all read" button when there are unread notifications', async () => {
    seedUnreadNotifications(2)
    renderTopBar()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Mark all read')).toBeInTheDocument()
    })
  })

  it('does not show the "Mark all read" button when there are no unread notifications', async () => {
    // No unread agent notifications and no persistent unread.
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    renderTopBar()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    expect(screen.queryByText('Mark all read')).not.toBeInTheDocument()
  })

  it('clicking "Mark all read" marks notifications read and does NOT close the drawer', async () => {
    seedUnreadNotifications(2)
    renderTopBar()

    fireEvent.click(getBellButton())

    const markAllButton = await screen.findByText('Mark all read')
    fireEvent.click(markAllButton)

    // Agent notifications are flushed to read.
    await waitFor(() => {
      const state = useNotificationStore.getState()
      expect(state.notifications.every((n) => n.read)).toBe(true)
    })

    // Drawer is still open.
    expect(screen.getByText('Notifications')).toBeInTheDocument()
  })

  it('clicking "Mark all read" also marks persistent notifications read on the server', async () => {
    // Server reports 1 persistent unread item so the persistent path runs.
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 1 }
      if (url === '/notifications')
        return [
          {
            id: 'p-1',
            type: 'agent',
            title: 'Agent done',
            body: 'finished work',
            action_label: null,
            action_url: null,
            read: false,
            created_at: new Date().toISOString(),
            metadata: {},
          },
        ]
      return {}
    })

    renderTopBar()

    // Wait for the persistent list to land in component state
    // (the bell badge appears once the effect runs).
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications')
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    const markAllButton = await screen.findByText('Mark all read')

    await act(async () => {
      fireEvent.click(markAllButton)
    })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/notifications/read-all')
    })

    // Drawer is still open.
    expect(screen.getByText('Notifications')).toBeInTheDocument()
  })

  it('clicking the backdrop closes the drawer and marks remaining unread items as read', async () => {
    seedUnreadNotifications(2)
    renderTopBar()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // The backdrop is the fixed inset-0 div that sits right before
    // the drawer panel. It is the first child of the relative wrapper
    // and lives at z-40. Find it by its class signature.
    const backdrop = document.querySelector('.fixed.inset-0.z-40') as HTMLElement
    expect(backdrop).toBeTruthy()

    fireEvent.click(backdrop)

    // Drawer is gone.
    await waitFor(() => {
      expect(screen.queryByText('Notifications')).not.toBeInTheDocument()
    })

    // Remaining unread items were flushed.
    const state = useNotificationStore.getState()
    expect(state.notifications.every((n) => n.read)).toBe(true)
  })

  it('clicking the X button closes the drawer and marks remaining unread items as read', async () => {
    seedUnreadNotifications(3)
    renderTopBar()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    // The X button is the only button in the drawer header containing
    // the "close" icon. Find it by walking from the close icon up.
    const closeIcon = Array.from(
      document.querySelectorAll('span.material-symbols-outlined')
    ).find((el) => el.textContent === 'close')
    expect(closeIcon).toBeTruthy()
    const xButton = closeIcon!.closest('button') as HTMLButtonElement
    expect(xButton).toBeTruthy()

    fireEvent.click(xButton)

    await waitFor(() => {
      expect(screen.queryByText('Notifications')).not.toBeInTheDocument()
    })

    const state = useNotificationStore.getState()
    expect(state.notifications.every((n) => n.read)).toBe(true)
  })
})

describe('TopBar What\'s New button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
      whatsNewLastSeen: '',
    })
    mockedApiGet.mockImplementation(async (url: string) => {
      if (url === '/notifications/unread/count') return { count: 0 }
      if (url === '/notifications') return []
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('the What\'s New sparkle button is no longer rendered inside the TopBar header', () => {
    renderTopBar()

    // The What's New button lives in the Sidebar now, not the header.
    // Confirm that no sparkle button is mounted inside the TopBar header.
    const header = document.querySelector('header')
    expect(header).toBeTruthy()
    const whatsNewButton = header!.querySelector('[data-testid="whats-new-button"]')
    expect(whatsNewButton).toBeNull()
  })
})
