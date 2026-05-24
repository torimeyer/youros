import { useState } from 'react'
import Icon from './Icon'
import { api, ApiError } from '../lib/api'

interface JiraCommentComposerProps {
  issueKey: string
  onCancel: () => void
  onSent: () => void
}

interface CommentResponse {
  ok: boolean
  error?: string
}

export default function JiraCommentComposer({
  issueKey,
  onCancel,
  onSent,
}: JiraCommentComposerProps) {
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleSend = async () => {
    setErrorMessage(null)
    if (body.trim().length === 0) return
    setSending(true)
    try {
      const res = await api.post<CommentResponse>(
        `/atlassian/jira/issue/${issueKey}/comment`,
        { body }
      )
      if (res.ok) {
        onSent()
        return
      }
      setErrorMessage(res.error || 'Could not post the comment. Please try again.')
    } catch (e: unknown) {
      const msg =
        e instanceof ApiError ? e.message : 'Could not post the comment. Please try again.'
      setErrorMessage(msg)
    } finally {
      setSending(false)
    }
  }

  return (
    <div data-testid="jira-comment-composer" className="mt-3 bg-slate-900/60 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon name="comment" size={16} className="text-blue-400" />
        <span className="text-sm font-medium text-slate-200">Comment on {issueKey}</span>
      </div>

      {errorMessage && (
        <div className="mb-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
          <p>{errorMessage}</p>
        </div>
      )}

      <textarea
        autoFocus
        data-testid="jira-comment-textarea"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={sending}
        placeholder="Write a comment..."
        rows={4}
        aria-label="Comment body"
        className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-slate-600 disabled:opacity-50 resize-y"
      />

      <div className="flex items-center justify-end mt-3 gap-2">
        <button
          type="button"
          data-testid="jira-comment-cancel"
          onClick={onCancel}
          disabled={sending}
          className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid="jira-comment-send"
          onClick={() => { void handleSend() }}
          disabled={sending || body.trim().length === 0}
          aria-label="Send comment"
          className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
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
  )
}
