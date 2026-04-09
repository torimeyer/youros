import { useState } from 'react'
import Icon from './Icon'
import { api, ApiError } from '../lib/api'

interface GmailReplyComposerProps {
  threadId: string
  inReplyToMessageId: string
  replyAll: boolean
  onCancel: () => void
  onSent: () => void
}

interface DraftResponse {
  ok: boolean
  draft?: string
  error?: string
}

interface ReplyResponse {
  ok: boolean
  message_id?: string
  error?: string
  needs_reauth?: boolean
}

export default function GmailReplyComposer({
  threadId,
  inReplyToMessageId,
  replyAll,
  onCancel,
  onSent,
}: GmailReplyComposerProps) {
  const [body, setBody] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [sending, setSending] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [reauthUrl, setReauthUrl] = useState<string | null>(null)

  const busy = drafting || sending

  const handleDraftForMe = async () => {
    setErrorMessage(null)
    if (body.trim().length > 0) {
      const confirmed = window.confirm('Replace your draft?')
      if (!confirmed) return
    }
    setDrafting(true)
    try {
      const res = await api.post<DraftResponse>('/gmail/draft_reply', {
        thread_id: threadId,
        in_reply_to_message_id: inReplyToMessageId,
      })
      if (res.ok && typeof res.draft === 'string') {
        setBody(res.draft)
      } else {
        setErrorMessage(res.error || 'Could not draft a reply. Please try again.')
      }
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? e.message : 'Could not draft a reply. Please try again.'
      setErrorMessage(msg)
    } finally {
      setDrafting(false)
    }
  }

  const handleSend = async () => {
    setErrorMessage(null)
    setReauthUrl(null)
    if (body.trim().length === 0) {
      setErrorMessage('Type a reply before sending.')
      return
    }
    setSending(true)
    try {
      const res = await api.post<ReplyResponse>('/gmail/reply', {
        thread_id: threadId,
        in_reply_to_message_id: inReplyToMessageId,
        body,
        reply_all: replyAll,
      })
      if (res.ok) {
        onSent()
        return
      }
      if (res.needs_reauth) {
        try {
          const cap = await api.get<{ has_send_scope: boolean; reauth_url: string | null }>(
            '/gmail/send_capability'
          )
          setReauthUrl(cap.reauth_url)
        } catch {
          setReauthUrl(null)
        }
        setErrorMessage('Reconnect Gmail to send replies.')
      } else {
        setErrorMessage(res.error || 'Could not send the reply. Please try again.')
      }
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        const data = e.response?.data?.detail as { needs_reauth?: boolean; reauth_url?: string } | undefined
        if (data?.needs_reauth) {
          setReauthUrl(data.reauth_url || null)
          setErrorMessage('Reconnect Gmail to send replies.')
        } else {
          setErrorMessage(e.message || 'Could not send the reply. Please try again.')
        }
      } else {
        setErrorMessage('Could not send the reply. Please try again.')
      }
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="mt-3 bg-slate-900/60 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon name="reply" size={16} className="text-blue-400" />
        <span className="text-sm font-medium text-slate-200">
          {replyAll ? 'Reply all' : 'Reply'}
        </span>
      </div>

      {errorMessage && (
        <div className="mb-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
          <p>{errorMessage}</p>
          {reauthUrl && (
            <a
              href={reauthUrl}
              className="inline-block mt-2 text-red-200 underline hover:text-red-100"
            >
              Reconnect Gmail
            </a>
          )}
        </div>
      )}

      <textarea
        autoFocus
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={busy}
        placeholder="Write your reply, or let myOS draft one for you."
        rows={6}
        aria-label="Reply body"
        className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-slate-600 disabled:opacity-50 resize-y"
      />

      <div className="flex items-center justify-between mt-3 gap-2 flex-wrap">
        <button
          type="button"
          onClick={handleDraftForMe}
          disabled={busy}
          aria-label="Draft for me"
          className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          {drafting ? (
            <Icon name="progress_activity" size={16} className="animate-spin" />
          ) : (
            <Icon name="auto_awesome" size={16} />
          )}
          Draft for me
        </button>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSend}
            disabled={busy || body.trim().length === 0}
            aria-label="Send reply"
            className="flex items-center gap-1.5 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            {sending ? (
              <Icon name="progress_activity" size={16} className="animate-spin" />
            ) : (
              <Icon name="send" size={16} />
            )}
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
