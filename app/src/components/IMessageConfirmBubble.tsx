interface Props {
  resolving: boolean
  displayName: string
  messageText: string
  onSend: () => void
  onDismiss: () => void
}

export function IMessageConfirmBubble({
  resolving,
  displayName,
  messageText,
  onSend,
  onDismiss,
}: Props) {
  if (resolving) {
    return (
      <div
        data-testid="imessage-confirm-bubble-resolving"
        className="inline-flex items-center gap-2 px-4 py-3 rounded-xl border border-slate-600/40 bg-slate-50/40 dark:bg-slate-800/40 text-sm text-slate-600 dark:text-slate-400 max-w-[85%]"
      >
        <span>Looking up contact...</span>
      </div>
    )
  }

  const preview = messageText.length > 120 ? `${messageText.slice(0, 120)}...` : messageText

  return (
    <div
      data-testid="imessage-confirm-bubble"
      className="inline-flex flex-col gap-3 px-4 py-3 rounded-xl border border-blue-500/30 bg-blue-500/5 text-sm text-slate-700 dark:text-slate-300 max-w-[85%]"
    >
      <div>
        <span className="text-xs text-slate-600 dark:text-slate-400 block mb-1">Send via iMessage?</span>
        <span className="font-medium text-blue-200">{displayName}</span>
        {preview && (
          <p className="text-slate-600 dark:text-slate-400 text-xs mt-1 italic">&ldquo;{preview}&rdquo;</p>
        )}
      </div>
      <div className="flex gap-2">
        <button
          data-testid="imessage-send-btn"
          onClick={onSend}
          aria-label={`Send to ${displayName} via iMessage`}
          className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
        >
          Send
        </button>
        <button
          data-testid="imessage-dismiss-btn"
          onClick={onDismiss}
          aria-label="Dismiss iMessage send, treat as regular chat"
          className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:text-white text-xs font-medium transition-colors"
        >
          Not now
        </button>
      </div>
    </div>
  )
}
