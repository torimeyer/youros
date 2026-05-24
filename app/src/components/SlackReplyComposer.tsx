import { useState } from 'react'
import { Link } from 'react-router-dom'
import Icon from './Icon'
import ConfirmModal from './ConfirmModal'
import { useConfirm } from '../hooks/useConfirm'
import { api, ApiError } from '../lib/api'

interface SlackReplyComposerProps {
  channelId: string
  ts: string
  onCancel: () => void
  onSent: () => void
}

interface DraftResponse {
  ok: boolean
  draft?: string
  error?: string
  needs_gemini?: boolean
}

interface ReplyResponse {
  ok: boolean
  error?: string
}

export default function SlackReplyComposer({
  channelId,
  ts,
  onCancel,
  onSent,
}: SlackReplyComposerProps) {
  const [body, setBody] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [sending, setSending] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [needsGemini, setNeedsGemini] = useState(false)
  const { confirm, confirmProps } = useConfirm()

  const busy = drafting || sending

  const handleDraftForMe = async () => {
    setErrorMessage(null)
    if (body.trim().length > 0) {
      const confirmed = await confirm({
        title: 'Replace your draft?',
        message: 'The text you typed will be replaced with an AI-generated reply.',
        confirmLabel: 'Replace',
        danger: false,
      })
      if (!confirmed) return
    }
    setDrafting(true)
    try {
      const res = await api.post<DraftResponse>('/slack/draft_reply', {
        channel_id: channelId,
        ts,
      })
      if (res.ok && typeof res.draft === 'string') {
        setBody(res.draft)
        setNeedsGemini(false)
      } else if (res.needs_gemini) {
        setNeedsGemini(true)
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
    if (body.trim().length === 0) {
      setErrorMessage('Type a reply before sending.')
      return
    }
    setSending(true)
    try {
      const res = await api.post<ReplyResponse>('/slack/reply', {
        channel_id: channelId,
        ts,
        text: body,
      })
      if (res.ok) {
        onSent()
        return
      }
      setErrorMessage(res.error || 'Could not send the reply. Please try again.')
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? e.message : 'Could not send the reply. Please try again.'
      setErrorMessage(msg)
    } finally {
      setSending(false)
    }
  }

  return (
    <div data-testid="slack-reply-composer" className="mt-3 bg-slate-900/60 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon name="reply" size={16} className="text-purple-400" />
        <span className="text-sm font-medium text-slate-200">Reply</span>
      </div>

      {needsGemini && (
        <div
          data-testid="slack-needs-gemini-message"
          className="mb-3 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-300"
        >
          Connect Gemini Enterprise in Settings to enable Slack drafts.{' '}
          <Link to="/settings" className="underline hover:text-amber-100">
            Go to Settings
          </Link>
        </div>
      )}

      {errorMessage && (
        <div className="mb-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
          <p>{errorMessage}</p>
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
          data-testid="slack-draft-button"
          onClick={handleDraftForMe}
          disabled={busy || needsGemini}
          aria-label="Draft reply"
          className="flex items-center gap-1.5 px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          {drafting ? (
            <Icon name="progress_activity" size={16} className="animate-spin" />
          ) : (
            <Icon name="auto_awesome" size={16} />
          )}
          Draft reply
        </button>
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="slack-cancel-button"
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="slack-send-button"
            onClick={handleSend}
            disabled={busy || body.trim().length === 0}
            aria-label="Send reply"
            className="flex items-center gap-1.5 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
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
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
