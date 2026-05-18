import Icon from './Icon'

export interface ReadinessCheck {
  name: string
  passed: boolean
  detail: string
}

interface GeminiReadyChipProps {
  ready: boolean
  checks?: ReadinessCheck[]
  onClick?: () => void
}

export function GeminiReadyChip({ ready, checks, onClick }: GeminiReadyChipProps) {
  if (ready) {
    return (
      <button
        data-testid="gemini-ready-chip"
        onClick={(e) => { e.stopPropagation(); onClick?.() }}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-violet-500/15 text-violet-400 border border-violet-500/30 hover:bg-violet-500/25 transition-colors"
        title="Gemini-ready — click to spawn Gemini agent"
      >
        <Icon name="auto_awesome" className="text-[10px]" />
        Gemini-ready
      </button>
    )
  }

  const failedChecks = (checks ?? []).filter((c) => !c.passed)
  const tooltip =
    failedChecks.length > 0
      ? `Not ready:\n${failedChecks.map((c) => `• ${c.name}: ${c.detail}`).join('\n')}`
      : 'Not Gemini-ready'

  return (
    <span
      data-testid="gemini-not-ready-chip"
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-500/15 text-slate-500 border border-slate-500/30 cursor-default"
      title={tooltip}
    >
      <Icon name="auto_awesome" className="text-[10px]" />
      Not ready
    </span>
  )
}
