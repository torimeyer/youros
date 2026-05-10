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
    window.localStorage.removeItem('myos.slackChannels.v2')
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

describe('Slack stale cache cleared on disconnect', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
  })

  it('clears channel cache when status returns disconnected', async () => {
    // Pre-seed stale channel data in localStorage (v2 format with team_id)
    window.localStorage.setItem(
      'myos.slackChannels.v2',
      JSON.stringify({ team_id: 'T_OLD', channels: [{ id: 'C_OLD', name: 'old-channel', is_private: false, num_members: 5, topic: '' }] })
    )

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

    expect(window.localStorage.getItem('myos.slackChannels.v2')).toBeNull()
  })

  it('clears channel cache after disconnect action', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: true, team_name: 'Acme', team_id: 'T1', configured: true })
      }
      if (path.includes('/slack/channels')) {
        return Promise.resolve({
          channels: [{ id: 'C1', name: 'general', is_private: false, num_members: 10, topic: '' }],
        })
      }
      return Promise.resolve({})
    })
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({})

    renderSlack()

    await waitFor(() => {
      expect(screen.getByText('general')).toBeInTheDocument()
    })

    // Verify v2 cache was written after channels loaded
    expect(window.localStorage.getItem('myos.slackChannels.v2')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Disconnect/i }))

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    expect(window.localStorage.getItem('myos.slackChannels.v2')).toBeNull()
  })
})

describe('→1063 stale workspace channels cleared on reconnect', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
  })

  it('does not show channels from prior workspace when team_id changes', async () => {
    // Pre-seed channels from workspace A (team_id T_OLD)
    window.localStorage.setItem(
      'myos.slackChannels.v2',
      JSON.stringify({
        team_id: 'T_OLD',
        channels: [
          { id: 'C_OLD', name: 'furniture', is_private: false, num_members: 3, topic: 'Trello board URL' },
        ],
      })
    )

    // Status returns workspace B (team_id T_NEW)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: true, team_name: 'New Corp', team_id: 'T_NEW', configured: true })
      }
      if (path.includes('/slack/channels')) {
        return Promise.resolve({
          channels: [{ id: 'C_NEW', name: 'engineering', is_private: false, num_members: 8, topic: '' }],
        })
      }
      return Promise.resolve({})
    })

    renderSlack()

    // The old-workspace channel must never appear
    await waitFor(() => {
      expect(screen.getByText('engineering')).toBeInTheDocument()
    })
    expect(screen.queryByText('furniture')).not.toBeInTheDocument()

    // Cache must now hold workspace B's team_id
    const raw = window.localStorage.getItem('myos.slackChannels.v2')
    const entry = JSON.parse(raw ?? '{}')
    expect(entry.team_id).toBe('T_NEW')
    expect(entry.channels[0].name).toBe('engineering')
  })
})

describe('Slack mrkdwn stripping in channel topics', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
  })

  it('renders plain URL from <url> mrkdwn syntax in channel topic', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: true, team_name: 'Acme', team_id: 'T1', configured: true })
      }
      if (path.includes('/slack/channels')) {
        return Promise.resolve({
          channels: [
            {
              id: 'C1',
              name: 'general',
              is_private: false,
              num_members: 10,
              topic: 'Check <https://example.com> for docs',
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByText('Check https://example.com for docs')).toBeInTheDocument()
    })
    expect(screen.queryByText(/\<https:\/\/example\.com\>/)).not.toBeInTheDocument()
  })

  it('renders link text from <url|text> mrkdwn syntax in channel topic', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: true, team_name: 'Acme', team_id: 'T1', configured: true })
      }
      if (path.includes('/slack/channels')) {
        return Promise.resolve({
          channels: [
            {
              id: 'C1',
              name: 'general',
              is_private: false,
              num_members: 10,
              topic: 'Visit <https://example.com|our site> today',
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByText('Visit our site today')).toBeInTheDocument()
    })
    expect(screen.queryByText(/our site\>/)).not.toBeInTheDocument()
  })
})

describe('Slack ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
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

describe('Slack one-click OAuth button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
  })

  it('shows "Enter credentials manually" link when configured=true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByTestId('slack-enter-credentials-link')).toBeInTheDocument()
    })
  })

  it('clicking "Enter credentials manually" shows the credential form', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByTestId('slack-enter-credentials-link')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('slack-enter-credentials-link'))

    await waitFor(() => {
      expect(screen.getByLabelText('Client ID')).toBeInTheDocument()
      expect(screen.getByLabelText('Client Secret')).toBeInTheDocument()
    })
  })

  it('shows "Back to Connect" link in the form when configured=true', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: true })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByTestId('slack-enter-credentials-link')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('slack-enter-credentials-link'))

    await waitFor(() => {
      expect(screen.getByTestId('slack-back-to-connect')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('slack-back-to-connect'))

    await waitFor(() => {
      expect(screen.getByTestId('slack-oauth-connect-btn')).toBeInTheDocument()
    })
  })

  it('does not show "Enter credentials manually" link when configured=false', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: false })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByLabelText('Client ID')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('slack-enter-credentials-link')).not.toBeInTheDocument()
  })
})

describe('Slack configure form', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.slackChannels.v2')
  })

  it('renders configure form when configured is false', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: false })
      }
      return Promise.resolve({})
    })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByLabelText('Client ID')).toBeInTheDocument()
      expect(screen.getByLabelText('Client Secret')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Save/i })).toBeInTheDocument()
    })
  })

  it('needle button calls /slack/triage/promote with channel_id and ts', async () => {
    mockConnectedWithMessages()
    renderSlack()
    await waitFor(() => {
      expect(screen.getByText('general')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('general'))
    await waitFor(() => {
      expect(screen.getByText('Hey team!')).toBeInTheDocument()
    })

    const needleBtn = screen.getByTestId('slack-needle-1000000001.000100')
    fireEvent.click(needleBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/slack/triage/promote', {
        channel_id: 'C1',
        ts: '1000000001.000100',
      })
    })
  })

  it('submitting the form calls /slack/configure with entered credentials', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/slack/status')) {
        return Promise.resolve({ connected: false, team_name: '', team_id: '', configured: false })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true, configured: true })

    renderSlack()

    await waitFor(() => {
      expect(screen.getByLabelText('Client ID')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'my-client-id' } })
    fireEvent.change(screen.getByLabelText('Client Secret'), { target: { value: 'my-client-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/i }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/slack/configure', {
        client_id: 'my-client-id',
        client_secret: 'my-client-secret',
      })
    })
  })
})
