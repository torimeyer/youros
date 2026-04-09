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

    // Wait for the persistent unread count to land in component state
    // (the bell badge appears once the effect runs).
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/notifications/unread/count')
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

describe('TopBar What\'s New drawer', () => {
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

  it('clicking the sparkle button opens the What\'s New drawer', async () => {
    renderTopBar()

    // Drawer is not visible yet.
    expect(screen.queryByTestId('whats-new-modal')).not.toBeInTheDocument()

    // Click the sparkle button.
    fireEvent.click(screen.getByTestId('whats-new-button'))

    // Drawer is now visible.
    await waitFor(() => {
      expect(screen.getByTestId('whats-new-modal')).toBeInTheDocument()
    })
  })

  it('clicking the sparkle button still opens the drawer when chat is open', async () => {
    useAppStore.setState({ chatOpen: true, chatWidth: 400 })
    renderTopBar()

    expect(screen.queryByTestId('whats-new-modal')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('whats-new-button'))

    await waitFor(() => {
      expect(screen.getByTestId('whats-new-modal')).toBeInTheDocument()
    })
  })

  it('What\'s New drawer does not get covered by the notifications backdrop', async () => {
    seedUnreadNotifications(1)
    renderTopBar()

    // Open the What's New drawer first.
    fireEvent.click(screen.getByTestId('whats-new-button'))
    await waitFor(() => {
      expect(screen.getByTestId('whats-new-modal')).toBeInTheDocument()
    })

    // The notifications drawer should not be open at the same time.
    expect(screen.queryByText('Notifications')).not.toBeInTheDocument()
  })

  it('What\'s New drawer renders into document.body via portal so it escapes the header stacking context', async () => {
    // This is a regression test for a bug where the drawer rendered inside
    // the fixed z-40 TopBar header. Because the header creates its own
    // stacking context, the drawer's z-50 was relative to the header and
    // got painted under root-level z-50 siblings (Sidebar, ChatPanel),
    // making the drawer invisible whenever chat was open. The fix uses
    // a React portal so the drawer mounts directly under document.body.
    useAppStore.setState({ chatOpen: true, chatWidth: 400 })
    renderTopBar()

    fireEvent.click(screen.getByTestId('whats-new-button'))

    const modal = await screen.findByTestId('whats-new-modal')

    // The drawer's nearest fixed-position ancestor must NOT be the TopBar
    // header. If it is, the drawer is trapped in the header's stacking
    // context and root-level z-50 elements (chat panel) will cover it.
    let parent: HTMLElement | null = modal.parentElement
    let foundHeader = false
    while (parent && parent !== document.body) {
      if (parent.tagName === 'HEADER') {
        foundHeader = true
        break
      }
      parent = parent.parentElement
    }
    expect(foundHeader).toBe(false)

    // The drawer must be a descendant of document.body, not orphaned.
    expect(document.body.contains(modal)).toBe(true)
  })
})
