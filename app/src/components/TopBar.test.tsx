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
      <TopBar />
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

describe('TopBar page title removed from header (→2105)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
  })

  it('does not render a page title heading in the top bar', () => {
    renderTopBar()
    expect(screen.queryByTestId('topbar-title')).not.toBeInTheDocument()
  })
})

describe('TopBar keyboard modifier key by platform', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ notifications: [], toastIds: [] })
    useNotificationsStore.setState({ notifications: [], wsConnected: false })
    useAppStore.setState({ osName: 'yourOS', chatOpen: false, chatWidth: 400 })
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
      osName: 'yourOS',
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

