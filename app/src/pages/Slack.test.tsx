import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Slack from './Slack'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  }
})

vi.mock('../lib/sidebarBus', async () => {
  const actual = await vi.importActual<typeof import('../lib/sidebarBus')>('../lib/sidebarBus')
  return {
    ...actual,
    notifyInboxChange: vi.fn(),
  }
})

vi.mock('../components/SlackReplyComposer', () => ({
  default: ({ onCancel }: { onCancel: () => void }) => (
    <div data-testid="mock-slack-reply-composer">
      <button onClick={onCancel}>Cancel</button>
    </div>
  ),
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
import { notifyInboxChange } from '../lib/sidebarBus'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedNotifyInboxChange = vi.mocked(notifyInboxChange)

function renderSlack() {
  return render(
    <MemoryRouter>
      <Slack />
    </MemoryRouter>
  )
}

function mockConnectedWithMessages() {
  mockedApiGet.mockImplementation((path: string) => {
    if (path.includes('/slack/status')) {
      return Promise.resolve({ connected: true, team_name: 'Acme', team_id: 'T1', configured: true })
    }
    if (path.includes('/slack/channels')) {
      return Promise.resolve({
        channels: [{ id: 'C1', name: 'general', is_private: false, num_members: 10, topic: '' }],
      })
    }
    if (path.includes('/slack/messages/C1')) {
      return Promise.resolve({
        messages: [
          { ts: '1000000001.000100', user: 'alice', text: 'Hey team!', type: 'message' },
        ],
      })
    }
    return Promise.resolve({})
  })
  mockedApiPost.mockResolvedValue({ ok: true })
}

describe('Slack flag + reply buttons', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v1')
  })

  async function renderAndOpenChannel() {
    mockConnectedWithMessages()
    renderSlack()
    await waitFor(() => {
      expect(screen.getByText('general')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('general'))
    await waitFor(() => {
      expect(screen.getByText('Hey team!')).toBeInTheDocument()
    })
  }

  it('flag button calls /slack/followups and notifies inbox', async () => {
    await renderAndOpenChannel()

    const flagBtn = screen.getByTestId('slack-flag-1000000001.000100')
    fireEvent.click(flagBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/slack/followups', {
        channel_id: 'C1',
        channel_name: 'general',
        ts: '1000000001.000100',
        user: 'alice',
        text: 'Hey team!',
      })
    })

    await waitFor(() => {
      expect(mockedNotifyInboxChange).toHaveBeenCalledTimes(1)
    })
  })

  it('reply button opens the SlackReplyComposer inline', async () => {
    await renderAndOpenChannel()

    expect(screen.queryByTestId('mock-slack-reply-composer')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('slack-reply-1000000001.000100'))

    await waitFor(() => {
      expect(screen.getByTestId('mock-slack-reply-composer')).toBeInTheDocument()
    })
  })

  it('clicking cancel on the composer closes it', async () => {
    await renderAndOpenChannel()

    fireEvent.click(screen.getByTestId('slack-reply-1000000001.000100'))
    await waitFor(() => {
      expect(screen.getByTestId('mock-slack-reply-composer')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByTestId('mock-slack-reply-composer')).not.toBeInTheDocument()
    })
  })
})

describe('Slack ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v1')
  })

  it('renders ConnectCard with purple accent when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // Purple accent color: #a855f7 -> jsdom converts to rgb(168, 85, 247)
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/168, 85, 247/)
  })

  it('shows Connect Slack title in ConnectCard', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: false })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByText('Connect Slack')).toBeInTheDocument()
    })
  })

  it('shows connect button when Slack is configured', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Slack workspace/i })).toBeInTheDocument()
    })
  })
})
