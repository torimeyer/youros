export interface PeerChatTurnsPickerProps {
  pendingId: string
  participants: string[]
  onPick: (turns: number) => void
}

const TURN_OPTIONS = [1, 2, 3] as const

export function PeerChatTurnsPicker({ participants, onPick }: PeerChatTurnsPickerProps) {
  const names = participants.map(p => p.charAt(0).toUpperCase() + p.slice(1))
  const label = names.length === 2
    ? `${names[0]} and ${names[1]}`
    : names.join(', ')

  return (
    <div
      data-testid="peer-chat-turns-picker"
      className="inline-flex flex-col gap-2 px-4 py-3 rounded-xl border border-blue-500/30 bg-blue-500/5 text-sm text-slate-300 max-w-[85%]"
    >
      <span className="text-xs text-slate-400">
        How many turns per AI for the {label} chat?
      </span>
      <div className="flex gap-2">
        {TURN_OPTIONS.map(n => (
          <button
            key={n}
            data-testid={`turns-option-${n}`}
            onClick={() => onPick(n)}
            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-blue-500/20 border border-slate-700 hover:border-blue-500/50 text-slate-300 hover:text-blue-200 text-xs font-medium transition-colors"
          >
            {n} {n === 1 ? 'turn' : 'turns'}
          </button>
        ))}
      </div>
    </div>
  )
}
