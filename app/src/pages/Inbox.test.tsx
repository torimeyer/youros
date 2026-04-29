import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Inbox from './Inbox'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedApiDelete = vi.mocked(api.delete)

const mockItems = [
  {
    id: 'slack:abc123',
    kind: 'slack',
    title: '#general',
    preview: 'Hey can you take a look at this PR',
    source: 'slack',
    source_ref: 'https://slack.com/archives/abc',
    permalink: 'https://slack.com/archives/abc',
    created_at: new Date().toISOString(),
    raw: { channel_id: 'C123', ts: '1234567890.000100' },
  },
  {
    id: 'task:task1',
    kind: 'task',
    title: 'Review deployment',
    preview: 'Check the staging deploy',
    source: 'email',
    source_ref: '',
    permalink: null,
    created_at: new Date().toISOString(),
    raw: {},
  },
]

const mockNotifications = [
  {
    id: 'notif-1',
    type: 'agent',
    title: 'Agent finished',
    body: 'Your build agent completed successfully.',
    action_url: 'https://example.com/agents/1',
    read: false,
    created_at: new Date().toISOString(),
  },
  {
    id: 'notif-2',
    type: 'spec_complete',
    title: 'Dashboard shipped',
    body: 'The new dashboard feature is live.',
    action_url: null,
    read: true,
    created_at: new Date().toISOString(),
  },
]

function renderInbox() {
  return render(
    <MemoryRouter>
      <Inbox />
    </MemoryRouter>
  )
}

describe('Inbox page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve([])
      return Promise.resolve({ items: mockItems })
    })
    mockedApiPost.mockResolvedValue({})
    mockedApiDelete.mockResolvedValue({})
  })

  it('renders inbox page with testid', async () => {
    renderInbox()
    expect(screen.getByTestId('inbox-page')).toBeInTheDocument()
  })

  it('renders items from API', async () => {
    renderInbox()
    await waitFor(() => {
      expect(screen.getByTestId('inbox-item-slack:abc123')).toBeInTheDocument()
      expect(screen.getByTestId('inbox-item-task:task1')).toBeInTheDocument()
    })
  })

  it('Open button calls window.open with permalink', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-item-slack:abc123'))
    const openButtons = screen.getAllByRole('button', { name: 'Open' })
    fireEvent.click(openButtons[0])
    expect(openSpy).toHaveBeenCalledWith('https://slack.com/archives/abc', '_blank')
  })

  it('Open button is disabled when no permalink', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-item-task:task1'))
    const openButtons = screen.getAllByRole('button', { name: 'Open' })
    const taskOpen = openButtons.find((btn) =>
      btn.closest('[data-testid="inbox-item-task:task1"]')
    )
    expect(taskOpen).toBeDisabled()
  })

  it('Convert to task calls POST /tasks then dismisses item', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-item-slack:abc123'))
    const convertButtons = screen.getAllByRole('button', { name: 'Convert to task' })
    fireEvent.click(convertButtons[0])
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/tasks',
        expect.objectContaining({ title: '#general', source: 'slack' })
      )
    })
    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/inbox/items/slack:abc123')
    })
  })

  it('Dismiss calls DELETE and removes item from list', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-item-slack:abc123'))
    const dismissButtons = screen.getAllByRole('button', { name: 'Dismiss' })
    fireEvent.click(dismissButtons[0])
    expect(mockedApiDelete).toHaveBeenCalledWith('/inbox/items/slack:abc123')
    await waitFor(() => {
      expect(screen.queryByTestId('inbox-item-slack:abc123')).not.toBeInTheDocument()
    })
  })

  it('shows empty state when no items', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve([])
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => {
      expect(screen.getByText('Your inbox is clear.')).toBeInTheDocument()
    })
  })

  it('renders source chip for each item', async () => {
    renderInbox()
    await waitFor(() => {
      expect(screen.getByText('Slack')).toBeInTheDocument()
      expect(screen.getByText('Email')).toBeInTheDocument()
    })
  })

  it('Reply button only appears on slack items', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-item-slack:abc123'))
    expect(screen.getByTestId('inbox-reply-slack:abc123')).toBeInTheDocument()
    expect(screen.queryByTestId('inbox-reply-task:task1')).not.toBeInTheDocument()
  })

  it('Reply button opens SlackReplyComposer inline', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-reply-slack:abc123'))
    fireEvent.click(screen.getByTestId('inbox-reply-slack:abc123'))
    await waitFor(() => {
      expect(screen.getByTestId('slack-reply-composer')).toBeInTheDocument()
    })
  })

  it('Cancel closes the composer', async () => {
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-reply-slack:abc123'))
    fireEvent.click(screen.getByTestId('inbox-reply-slack:abc123'))
    await waitFor(() => screen.getByTestId('slack-reply-composer'))
    fireEvent.click(screen.getByTestId('slack-cancel-button'))
    await waitFor(() => {
      expect(screen.queryByTestId('slack-reply-composer')).not.toBeInTheDocument()
    })
  })

  it('onSent dismisses the item', async () => {
    mockedApiPost.mockResolvedValue({ ok: true })
    renderInbox()
    await waitFor(() => screen.getByTestId('inbox-reply-slack:abc123'))
    fireEvent.click(screen.getByTestId('inbox-reply-slack:abc123'))
    await waitFor(() => screen.getByTestId('slack-reply-composer'))
    fireEvent.change(screen.getByLabelText('Reply body'), {
      target: { value: 'Sounds good!' },
    })
    fireEvent.click(screen.getByTestId('slack-send-button'))
    await waitFor(() => {
      expect(screen.queryByTestId('inbox-item-slack:abc123')).not.toBeInTheDocument()
    })
  })

  // Notification tests
  it('notifications section renders when notifications exist', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve(mockNotifications)
      return Promise.resolve({ items: mockItems })
    })
    renderInbox()
    await waitFor(() => {
      expect(screen.getByTestId('notification-item-notif-1')).toBeInTheDocument()
      expect(screen.getByTestId('notification-item-notif-2')).toBeInTheDocument()
    })
    expect(screen.getByText('Notifications')).toBeInTheDocument()
  })

  it('notification unread dot shown when read is false', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve(mockNotifications)
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => screen.getByTestId('notif-unread-dot-notif-1'))
    expect(screen.queryByTestId('notif-unread-dot-notif-2')).not.toBeInTheDocument()
  })

  it('notification type badge shows plain-English label', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve(mockNotifications)
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => {
      expect(screen.getByText('Agent')).toBeInTheDocument()
      expect(screen.getByText('Feature live')).toBeInTheDocument()
    })
  })

  it('notification dismiss calls DELETE and read, removes row', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve(mockNotifications)
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => screen.getByTestId('notif-dismiss-notif-1'))
    fireEvent.click(screen.getByTestId('notif-dismiss-notif-1'))
    expect(mockedApiDelete).toHaveBeenCalledWith('/notifications/notif-1')
    expect(mockedApiPost).toHaveBeenCalledWith('/notifications/notif-1/read', {})
    await waitFor(() => {
      expect(screen.queryByTestId('notification-item-notif-1')).not.toBeInTheDocument()
    })
  })

  it('empty state shows only when both inbox and notifications are empty', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve([])
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => {
      expect(screen.getByText('Your inbox is clear.')).toBeInTheDocument()
    })
  })

  it('no empty state when inbox empty but notifications exist', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/notifications') return Promise.resolve(mockNotifications)
      return Promise.resolve({ items: [] })
    })
    renderInbox()
    await waitFor(() => {
      expect(screen.queryByText('Your inbox is clear.')).not.toBeInTheDocument()
      expect(screen.getByTestId('notification-item-notif-1')).toBeInTheDocument()
    })
  })
})
