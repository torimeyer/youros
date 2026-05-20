import Icon from './Icon'

export interface ReadinessCheck {
  name: string
  passed: boolean
  detail: string
}

interface NeedsClarityChipProps {
  checks?: ReadinessCheck[]
}

export function NeedsClarityChip({ checks }: NeedsClarityChipProps) {
  const allChecks = checks ?? []
  if (allChecks.length === 0) return null

  const checkLines = allChecks.map((c) => `${c.passed ? '✓' : '✗'} ${c.name}: ${c.detail}`)
  const tooltipText = checkLines.join('\n')

  return (
    <span
      data-testid="needs-clarity-chip"
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30 cursor-default"
      title={tooltipText}
    >
      <Icon name="warning" className="text-[10px]" />
      Needs clarity
      <span data-testid="needs-clarity-tooltip" className="sr-only">
        {checkLines.join('; ')}
      </span>
    </span>
  )
}
