import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import TopBar from './TopBar'
import { useNotificationStore } from '../stores/notifications'
import { useNotificationsStore } from '../stores/notificationsStore'
import { useAppStore } from '../stores/app'

// Mock the api module so HTTP endpoints do not hit the network.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// Mock useNotificationsFeed so the WS connection does not run in tests.
// Tests populate useNotificationsStore directly to control badge/drawer state.
vi.mock('../hooks/useNotificationsFeed', () => ({
  useNotificationsFeed: vi.fn(),
}))

// Mock push notifications module so tests don't touch real Push API
vi.mock('../lib/pushNotifications', () => ({
  isPushSupported: vi.fn().mockReturnValue(false),
  isSubscribed: vi.fn().mockResolvedValue(false),
  subscribe: vi.fn().mockResolvedValue(false),
  unsubscribe: vi.fn().mockResolvedValue(false),
}))

import { api } from '../lib/api'

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so the responsive desktop detection in TopBar does not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

const mockedApiPost = vi.mocked(api.post)

function renderTopBar() {
  return render(
    <BrowserRouter>
      <TopBar title="Home" />
    </BrowserRouter>
  )
}

// Helper that seeds the agent notification store with `count` unread items.
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

// Build a WS notification fixture.
function makeWsNotif(id: string, read = false) {
  return {
    id,
    type: 'agent',
    title: `Item ${id}`,
    body: `body ${id}`,
    action_label: null as string | null,
    action_url: null as string | null,
    read,
    created_at: new Date().toISOString(),
  }
}

// Find the notifications bell button by walking up from its icon span.
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
function getRenderedItemCount(): number {
  const emptyState = document.querySelector('.p-6.text-center')
  if (emptyState) return 0
  const listContainer = document.querySelector('.max-h-80.overflow-y-auto')
  if (!listContainer) return 0
  return listContainer.children.length
}

// Count the rendered UNREAD list items inside the dropdown body.
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

// The core invariant: the badge on the bell must always equal the number
// of UNREAD items the dropdown would render.
function assertBadgeMatchesDropdown(context: string) {
  const badge = getBadgeCount()
  const drawerOpen = !!screen.queryByText('Notifications')
  if (!drawerOpen) return
  const unreadVisible = getRenderedUnreadCount()
  expect(
    badge,
    `${context}: badge=${badge} but dropdown shows ${unreadVisible} unread items`
  ).toBe(unreadVisible)
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
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('Case A: only agent store has unread, badge equals dropdown unread count', async () => {
    seedUnreadNotifications(3)
    // No persistent notifications.
    useNotificationsStore.setState({ notifications: [], wsConnected: false })

    renderTopBar()
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

  it('Case B: only persistent store has items, badge equals dropdown unread count', async () => {
    useNotificationsStore.setState({
      notifications: [makeWsNotif('p-1'), makeWsNotif('p-2')],
      wsConnected: true,
    })

    renderTopBar()

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

  it('Case C: persistent store is empty, badge must be 0', async () => {
    useNotificationsStore.setState({ notifications: [], wsConnected: true })

    renderTopBar()

    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })
    expect(getBadgeText()).toBeNull()

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    expect(screen.getByText(/You.*all caught up/i)).toBeInTheDocument()
    assertBadgeMatchesDropdown('Case C empty store')
  })

  it('Mark all read zeroes the badge and shows the empty state branch for unread', async () => {
    seedUnreadNotifications(2)
    useNotificationsStore.setState({
      notifications: [makeWsNotif('p-1')],
      wsConnected: true,
    })

    renderTopBar()

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

    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })

    expect(screen.getByText('Notifications')).toBeInTheDocument()
    assertBadgeMatchesDropdown('after mark all read')
  })

  it('opening the drawer does not change the badge count', async () => {
    seedUnreadNotifications(4)
    useNotificationsStore.setState({ notifications: [], wsConnected: false })

    renderTopBar()

    const before = getBadgeCount()
    expect(before).toBe(4)

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    expect(getBadgeCount()).toBe(before)
    assertBadgeMatchesDropdown('after open, no mark read')
  })

  it('invariant holds when both sources have a mix of read and unread items', async () => {
    useNotificationStore.setState({
      notifications: [
        { id: 'a-1', agentName: 'one', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: false },
        { id: 'a-2', agentName: 'two', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: false },
        { id: 'a-3', agentName: 'three', prevStatus: 'spawned', status: 'completed', timestamp: new Date().toISOString(), read: true },
      ],
      toastIds: [],
    })
    // Persistent: 1 unread + 2 read.
    useNotificationsStore.setState({
      notifications: [
        makeWsNotif('p-1', false),
        makeWsNotif('p-2', true),
        makeWsNotif('p-3', true),
      ],
      wsConnected: true,
    })

    renderTopBar()

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

    expect(getRenderedItemCount()).toBe(6)
    expect(getRenderedUnreadCount()).toBe(3)
    assertBadgeMatchesDropdown('mixed read and unread in both sources')
  })

  it('invariant holds when the server returns zero unread but stale items exist', async () => {
    useNotificationsStore.setState({
      notifications: [makeWsNotif('p-1', true), makeWsNotif('p-2', true)],
      wsConnected: true,
    })

    renderTopBar()

    await waitFor(() => {
      expect(getBadgeCount()).toBe(0)
    })

    await act(async () => {
      fireEvent.click(getBellButton())
    })

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    expect(getRenderedItemCount()).toBe(2)
    expect(getRenderedUnreadCount()).toBe(0)
    assertBadgeMatchesDropdown('all stale items already read')
  })
})

describe('TopBar notifications drawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('clicking the bell icon opens the drawer without marking anything read', async () => {
    seedUnreadNotifications(3)
    renderTopBar()

    expect(screen.queryByText('Notifications')).not.toBeInTheDocument()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    const state = useNotificationStore.getState()
    expect(state.notifications.every((n) => !n.read)).toBe(true)

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

    await waitFor(() => {
      const state = useNotificationStore.getState()
      expect(state.notifications.every((n) => n.read)).toBe(true)
    })

    expect(screen.getByText('Notifications')).toBeInTheDocument()
  })

  it('clicking "Mark all read" also marks persistent notifications read on the server', async () => {
    // Seed one persistent unread item via the WS store.
    useNotificationsStore.setState({
      notifications: [makeWsNotif('p-1')],
      wsConnected: true,
    })

    renderTopBar()

    await waitFor(() => {
      expect(getBadgeCount()).toBe(1)
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

    expect(screen.getByText('Notifications')).toBeInTheDocument()
  })

  it('clicking the backdrop closes the drawer and marks remaining unread items as read', async () => {
    seedUnreadNotifications(2)
    renderTopBar()

    fireEvent.click(getBellButton())

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument()
    })

    const backdrop = document.querySelector('.fixed.inset-0.z-40') as HTMLElement
    expect(backdrop).toBeTruthy()

    fireEvent.click(backdrop)

    await waitFor(() => {
      expect(screen.queryByText('Notifications')).not.toBeInTheDocument()
    })

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
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
      whatsNewLastSeen: '',
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('the What\'s New sparkle button is no longer rendered inside the TopBar header', () => {
    renderTopBar()

    const header = document.querySelector('header')
    expect(header).toBeTruthy()
    const whatsNewButton = header!.querySelector('[data-testid="whats-new-button"]')
    expect(whatsNewButton).toBeNull()
  })
})


// ---------------------------------------------------------------------------
// Platform-specific modifier key display
// ---------------------------------------------------------------------------

describe('TopBar keyboard modifier key by platform', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
    useAppStore.setState({ osName: 'myOS', chatOpen: false, chatWidth: 400 })
  })

  it('shows Ctrl+ on a non-Mac platform (Win32)', async () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'Win32',
      configurable: true,
      writable: true,
    })
    renderTopBar()
    await act(async () => {})
    expect(document.body.textContent).toContain('Ctrl+')
    expect(document.body.textContent).not.toContain('⌘')
    Object.defineProperty(navigator, 'platform', { value: '', configurable: true, writable: true })
  })

  it('shows ⌘ on macOS (MacIntel)', async () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'MacIntel',
      configurable: true,
      writable: true,
    })
    vi.resetModules()

    const { createElement } = await import('react')
    const { render: r, screen: s, cleanup, waitFor: wf } = await import('@testing-library/react')
    const { BrowserRouter: BR } = await import('react-router-dom')
    const { default: FreshTopBar } = await import('./TopBar')

    r(createElement(BR as any, null, createElement(FreshTopBar as any, { title: 'Home' })))

    await wf(() => expect(document.body.textContent).toContain('⌘'))
    expect(document.body.textContent).not.toContain('Ctrl+')

    cleanup()
    Object.defineProperty(navigator, 'platform', { value: '', configurable: true, writable: true })
    vi.resetModules()
  })
})

describe('TopBar persistent-notification toast', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    useNotificationStore.setState({
      notifications: [],
      toastIds: [],
      firedKeys: new Set<string>(),
      persistentToastIds: new Set<string>(),
    })
    useNotificationsStore.setState({ notifications: [], wsConnected: false, snapshotReceived: false })
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('does not toast rows that were already in the store at mount', async () => {
    // Pre-seed the store before rendering with snapshotReceived: true so
    // the component can seed seenNotifIdsRef from the existing list and
    // NOT toast rows that predate the render.
    useNotificationsStore.setState({
      notifications: [
        {
          id: 'existing-1',
          type: 'roadmap_ready',
          title: 'Roadmap ready',
          body: '',
          action_label: null,
          action_url: '/files',
          read: false,
          created_at: new Date().toISOString(),
        },
      ],
      wsConnected: true,
      snapshotReceived: true,
    })

    renderTopBar()

    // Let effects settle.
    await act(async () => {})

    const state = useNotificationStore.getState()
    expect(state.toastIds).toHaveLength(0)
    expect(state.persistentToastIds.has('existing-1')).toBe(false)
  })

  it('fires a toast when a new roadmap_ready notification arrives via WS', async () => {
    // Start with empty store, render, then simulate the first (empty) WS
    // snapshot so seenNotifIdsRef seeds. After that, a NEW notification
    // arriving should fire a toast.
    renderTopBar()
    await act(async () => {})

    // First snapshot — empty list — primes the seen-set.
    await act(async () => {
      useNotificationsStore.setState({
        notifications: [],
        wsConnected: true,
        snapshotReceived: true,
      })
    })

    // Now a NEW notification arrives via WS.
    await act(async () => {
      useNotificationsStore.setState({
        notifications: [
          {
            id: 'new-roadmap-1',
            type: 'roadmap_ready',
            title: 'Your roadmap is ready',
            body: 'See /files/roadmap.md',
            action_label: 'Open',
            action_url: '/files',
            read: false,
            created_at: new Date().toISOString(),
          },
        ],
        wsConnected: true,
        snapshotReceived: true,
      })
    })

    await waitFor(() => {
      const s = useNotificationStore.getState()
      expect(s.persistentToastIds.has('new-roadmap-1')).toBe(true)
    })

    const s = useNotificationStore.getState()
    expect(s.toastIds).toContain('new-roadmap-1')
    expect(s.notifications[0].agentName).toBe('Your roadmap is ready')
    expect(s.notifications[0].status).toBe('roadmap_ready')
  })

  it('does not toast notification types outside the allow-list', async () => {
    renderTopBar()
    await act(async () => {})

    // Simulate WS snapshot with a non-allow-listed type.
    await act(async () => {
      useNotificationsStore.setState({
        notifications: [
          {
            id: 'other-1',
            type: 'other',
            title: 'Something minor',
            body: '',
            action_label: null,
            action_url: null,
            read: false,
            created_at: new Date().toISOString(),
          },
        ],
        wsConnected: true,
        snapshotReceived: true,
      })
    })

    // Give effects a tick to settle.
    await act(async () => {})

    const s = useNotificationStore.getState()
    expect(s.persistentToastIds.has('other-1')).toBe(false)
    expect(s.toastIds).not.toContain('other-1')
  })

  describe('Start tour button', () => {
    it('shows Start tour button when tourComplete is false', () => {
      useAppStore.setState({ tourComplete: false })
      renderTopBar()
      expect(screen.getByTestId('start-tour-btn')).toBeInTheDocument()
    })

    it('does not show Start tour button when tourComplete is true', () => {
      useAppStore.setState({ tourComplete: true })
      renderTopBar()
      expect(screen.queryByTestId('start-tour-btn')).toBeNull()
    })

    it('clicking Start tour button calls setShowTour(true)', () => {
      useAppStore.setState({ tourComplete: false, showTour: false })
      renderTopBar()
      fireEvent.click(screen.getByTestId('start-tour-btn'))
      expect(useAppStore.getState().showTour).toBe(true)
    })
  })

  describe('Team page entry point removed', () => {
    it('topbar-team-link is not rendered', () => {
      renderTopBar()
      expect(screen.queryByTestId('topbar-team-link')).toBeNull()
    })

    it('no button navigates to /team', () => {
      renderTopBar()
      const buttons = screen.getAllByRole('button')
      buttons.forEach((btn) => {
        expect(btn.getAttribute('data-testid')).not.toBe('topbar-team-link')
      })
    })
  })
})

// Regression guard: TopBar must render a flow spacer so pages don't need
// per-page top padding that can be forgotten (→1731).
describe('TopBar flow spacer', () => {
  it('renders a topbar-spacer div matching the fixed header height', () => {
    render(
      <BrowserRouter>
        <TopBar title="Test" />
      </BrowserRouter>
    )
    const spacer = document.querySelector('[data-testid="topbar-spacer"]')
    expect(spacer).not.toBeNull()
    // Must carry both mobile (h-14) and sm (h-16) height classes so
    // content is never hidden under the fixed bar on any breakpoint.
    expect(spacer?.className).toContain('h-14')
    expect(spacer?.className).toContain('sm:h-16')
  })
})
