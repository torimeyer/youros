import { useTimeStatus } from '../hooks/useTimeStatus'

interface Props {
  opKind: string
}

/**
 * Shows a small "~Nm" chip when there is a Time primitive estimate for the
 * given operation kind. Renders nothing when no estimate is available (not
 * enough history yet) or when the estimate rounds to zero minutes.
 *
 * Plain language only — never shows "p50", "seconds", or any stat jargon.
 */
export function TimePill({ opKind }: Props) {
  const status = useTimeStatus(opKind)

  if (!status || status.estimate_sec <= 0) return null

  const minutes = Math.round(status.estimate_sec / 60)
  if (minutes < 1) return null

  return (
    <span
      data-testid={`time-pill-${opKind}`}
      className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-700/50 text-slate-400 border border-slate-600/40"
      title="Typical time for this kind of work"
    >
      ~{minutes}m
    </span>
  )
}
