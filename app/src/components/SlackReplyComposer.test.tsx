import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SlackReplyComposer from './SlackReplyComposer'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

import { api } from '../lib/api'

const mockedApiPost = vi.mocked(api.post)

function renderComposer(props?: Partial<React.ComponentProps<typeof SlackReplyComposer>>) {
  return render(
    <MemoryRouter>
      <SlackReplyComposer
        channelId="C123"
        ts="1234567890.000100"
        onCancel={vi.fn()}
        onSent={vi.fn()}
        {...props}
      />
    </MemoryRouter>
  )
}

describe('SlackReplyComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the composer with draft and send buttons', () => {
    renderComposer()
    expect(screen.getByTestId('slack-reply-composer')).toBeInTheDocument()
    expect(screen.getByTestId('slack-draft-button')).toBeInTheDocument()
    expect(screen.getByTestId('slack-send-button')).toBeInTheDocument()
    expect(screen.getByTestId('slack-cancel-button')).toBeInTheDocument()
  })

  it('calls /slack/draft_reply with the right args and fills the textarea', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/slack/draft_reply')) {
        return Promise.resolve({ ok: true, draft: 'Sounds great, thanks for the heads up.' })
      }
      return Promise.resolve({})
    })

    renderComposer()

    fireEvent.click(screen.getByTestId('slack-draft-button'))

    await waitFor(() => {
      const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
      expect(textarea.value).toBe('Sounds great, thanks for the heads up.')
    })

    expect(mockedApiPost).toHaveBeenCalledWith('/slack/draft_reply', {
      channel_id: 'C123',
      ts: '1234567890.000100',
    })
  })

  it('calls /slack/reply with the typed body then fires onSent', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/slack/reply')) {
        return Promise.resolve({ ok: true })
      }
      return Promise.resolve({})
    })

    const onSent = vi.fn()
    renderComposer({ onSent })

    const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'On it, will circle back shortly.' } })

    fireEvent.click(screen.getByTestId('slack-send-button'))

    await waitFor(() => {
      expect(onSent).toHaveBeenCalledTimes(1)
    })

    expect(mockedApiPost).toHaveBeenCalledWith('/slack/reply', {
      channel_id: 'C123',
      ts: '1234567890.000100',
      text: 'On it, will circle back shortly.',
    })
  })

  it('shows the needs_gemini message and disables the draft button when Gemini is not connected', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/slack/draft_reply')) {
        return Promise.resolve({
          ok: false,
          error: 'Connect Gemini Enterprise in Settings to enable Slack drafts.',
          needs_gemini: true,
        })
      }
      return Promise.resolve({})
    })

    renderComposer()

    fireEvent.click(screen.getByTestId('slack-draft-button'))

    await waitFor(() => {
      expect(screen.getByTestId('slack-needs-gemini-message')).toBeInTheDocument()
    })

    expect(screen.getByTestId('slack-needs-gemini-message').textContent).toContain(
      'Connect Gemini Enterprise in Settings to enable Slack drafts.'
    )

    expect(screen.getByTestId('slack-draft-button')).toBeDisabled()
  })

  it('fires onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn()
    renderComposer({ onCancel })
    fireEvent.click(screen.getByTestId('slack-cancel-button'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
