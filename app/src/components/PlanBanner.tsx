import { useState } from 'react'

interface Props {
  plan: string
  onConfirm: () => void
  onCancel: () => void
  onRevise: (comment: string) => void
}

export function PlanBanner({ plan, onConfirm, onCancel, onRevise }: Props) {
  const [revisingMode, setRevisingMode] = useState(false)
  const [comment, setComment] = useState('')

  return (
    <div
      data-testid="plan-banner"
      className="inline-flex flex-col gap-3 px-4 py-3 rounded-xl border border-amber-500/30 bg-amber-500/5 text-sm text-slate-700 dark:text-slate-300 max-w-[85%]"
    >
      <div>
        <span className="text-xs text-amber-600 dark:text-amber-400 block mb-2 font-medium">Plan to run</span>
        <div className="text-slate-800 dark:text-slate-200 text-xs whitespace-pre-wrap leading-relaxed">{plan}</div>
      </div>
      {revisingMode ? (
        <div className="flex flex-col gap-2">
          <input
            data-testid="plan-banner-revise-input"
            autoFocus
            type="text"
            value={comment}
            onChange={e => setComment(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && comment.trim()) {
                onRevise(comment.trim())
              }
            }}
            placeholder="What should change?"
            className="px-3 py-1.5 rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-slate-800 dark:text-slate-200 text-xs placeholder-slate-500 outline-none focus:border-amber-500/60"
          />
          <div className="flex gap-2">
            <button
              data-testid="plan-banner-revise-submit"
              onClick={() => comment.trim() && onRevise(comment.trim())}
              className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium transition-colors"
            >
              Send revision
            </button>
            <button
              onClick={() => { setRevisingMode(false); setComment('') }}
              className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-xs font-medium transition-colors"
            >
              Back
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            data-testid="plan-banner-confirm"
            onClick={onConfirm}
            className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium transition-colors"
          >
            Approve
          </button>
          <button
            data-testid="plan-banner-revise"
            onClick={() => setRevisingMode(true)}
            className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:text-white text-xs font-medium transition-colors"
          >
            Revise
          </button>
          <button
            data-testid="plan-banner-cancel"
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:text-white text-xs font-medium transition-colors"
          >
            Reject
          </button>
        </div>
      )}
    </div>
  )
}
