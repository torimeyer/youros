import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import GmailReplyComposer from './GmailReplyComposer'

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
const mockedApiGet = vi.mocked(api.get)

describe('GmailReplyComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('calls the draft endpoint and fills the textarea on success', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/draft_reply')) {
        return Promise.resolve({ ok: true, draft: 'Hi there, thanks for the update.' })
      }
      return Promise.resolve({})
    })

    const onCancel = vi.fn()
    const onSent = vi.fn()
    render(
      <GmailReplyComposer
        threadId="t-1"
        inReplyToMessageId="m-1"
        replyAll={false}
        onCancel={onCancel}
        onSent={onSent}
      />
    )

    const draftButton = screen.getByRole('button', { name: /Draft for me/i })
    fireEvent.click(draftButton)

    await waitFor(() => {
      const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
      expect(textarea.value).toBe('Hi there, thanks for the update.')
    })

    expect(mockedApiPost).toHaveBeenCalledWith('/gmail/draft_reply', {
      thread_id: 't-1',
      in_reply_to_message_id: 'm-1',
    })
  })

  it('calls the reply endpoint with the typed body and the right reply_all flag, then fires onSent', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/reply')) {
        return Promise.resolve({ ok: true, message_id: 'sent-1' })
      }
      return Promise.resolve({})
    })

    const onCancel = vi.fn()
    const onSent = vi.fn()
    render(
      <GmailReplyComposer
        threadId="t-1"
        inReplyToMessageId="m-1"
        replyAll
        onCancel={onCancel}
        onSent={onSent}
      />
    )

    const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Sounds good, thanks.' } })

    const sendButton = screen.getByRole('button', { name: 'Send reply' })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(onSent).toHaveBeenCalledTimes(1)
    })

    expect(mockedApiPost).toHaveBeenCalledWith('/gmail/reply', {
      thread_id: 't-1',
      in_reply_to_message_id: 'm-1',
      body: 'Sounds good, thanks.',
      reply_all: true,
    })
  })

  it('shows an inline error without losing typed text when the backend returns ok:false', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/reply')) {
        return Promise.resolve({ ok: false, error: 'Gmail is having a moment. Try again in a bit.' })
      }
      return Promise.resolve({})
    })

    const onCancel = vi.fn()
    const onSent = vi.fn()
    render(
      <GmailReplyComposer
        threadId="t-1"
        inReplyToMessageId="m-1"
        replyAll={false}
        onCancel={onCancel}
        onSent={onSent}
      />
    )

    const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Important draft' } })

    fireEvent.click(screen.getByRole('button', { name: 'Send reply' }))

    await waitFor(() => {
      expect(
        screen.getByText('Gmail is having a moment. Try again in a bit.')
      ).toBeInTheDocument()
    })

    const preserved = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    expect(preserved.value).toBe('Important draft')
    expect(onSent).not.toHaveBeenCalled()
  })

  it('renders the Reconnect Gmail link with the real Google OAuth URL as href after a needs_reauth send failure', async () => {
    const googleOauthUrl =
      'https://accounts.google.com/o/oauth2/v2/auth?client_id=test&scope=gmail.send&state=xyz'

    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/reply')) {
        return Promise.resolve({ ok: false, needs_reauth: true })
      }
      return Promise.resolve({})
    })
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/gmail/send_capability')) {
        return Promise.resolve({ has_send_scope: false, reauth_url: googleOauthUrl })
      }
      return Promise.resolve({})
    })

    render(
      <GmailReplyComposer
        threadId="t-1"
        inReplyToMessageId="m-1"
        replyAll={false}
        onCancel={vi.fn()}
        onSent={vi.fn()}
      />
    )

    const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Hi there' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send reply' }))

    const link = await screen.findByRole('link', { name: /Reconnect Gmail/i })

    const href = link.getAttribute('href') || ''

    // The critical assertion: the Reconnect Gmail link must navigate to
    // the real Google consent screen, NOT to the JSON-returning API
    // endpoint that caused Tori to see raw JSON in the browser.
    expect(href).toBe(googleOauthUrl)
    expect(href.startsWith('https://accounts.google.com/')).toBe(true)
    expect(href).not.toBe('/api/drive/auth/url/gmail')
    expect(href.startsWith('/api/')).toBe(false)

    // And the rendered DOM must not contain a JSON envelope with a
    // "url" key, which is the symptom Tori reported.
    expect(document.body.textContent).not.toMatch(/\{\s*"url"\s*:/)
  })

  it('prompts before replacing an existing draft when Draft for me is clicked', async () => {
    mockedApiPost.mockImplementation((path: string) => {
      if (path.includes('/gmail/draft_reply')) {
        return Promise.resolve({ ok: true, draft: 'AI draft' })
      }
      return Promise.resolve({})
    })

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    render(
      <GmailReplyComposer
        threadId="t-1"
        inReplyToMessageId="m-1"
        replyAll={false}
        onCancel={vi.fn()}
        onSent={vi.fn()}
      />
    )

    const textarea = screen.getByLabelText('Reply body') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'My own words' } })

    fireEvent.click(screen.getByRole('button', { name: /Draft for me/i }))

    expect(confirmSpy).toHaveBeenCalledWith('Replace your draft?')
    // Cancelled, so the body stays and no draft_reply call goes out.
    expect((screen.getByLabelText('Reply body') as HTMLTextAreaElement).value).toBe('My own words')
    expect(mockedApiPost).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })
})
