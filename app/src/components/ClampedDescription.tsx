import { useState } from 'react'

// Mapping to avoid Tailwind purging dynamic `line-clamp-${n}` class strings.
const CLAMP_CLASS: Record<number, string> = {
  1: 'line-clamp-1',
  2: 'line-clamp-2',
  3: 'line-clamp-3',
  4: 'line-clamp-4',
  5: 'line-clamp-5',
  6: 'line-clamp-6',
}

interface Props {
  text: string
  /** Number of lines visible before clamping. Default 2. */
  lines?: number
  /** Tailwind classes for the text element (defaults to muted small text). */
  textClassName?: string
  /** data-testid for the expand/collapse button. */
  testId?: string
}

export function ClampedDescription({
  text,
  lines = 2,
  textClassName = 'text-xs text-slate-400',
  testId = 'card-expand',
}: Props) {
  const [expanded, setExpanded] = useState(false)
  if (!text) return null

  const clampClass = CLAMP_CLASS[lines] ?? 'line-clamp-2'

  return (
    <div className="flex flex-col gap-1 min-w-0">
      <div
        className={`whitespace-pre-wrap break-words ${expanded ? '' : clampClass} ${textClassName}`}
      >
        {text}
      </div>
      <button
        type="button"
        data-testid={testId}
        onClick={(e) => {
          e.stopPropagation()
          setExpanded((v) => !v)
        }}
        className="text-xs text-slate-500 hover:text-slate-300 self-start"
      >
        {expanded ? 'Show less' : 'Show more'}
      </button>
    </div>
  )
}
