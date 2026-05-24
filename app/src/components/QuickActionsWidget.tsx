import { useNavigate } from 'react-router-dom'
import { Card } from './ui'
import Icon from './Icon'

interface Props {
  onAddNeedle: () => void
}

const ACTIONS = [
  { icon: 'checklist', label: 'Open Backlog', color: 'text-blue-400', hoverBorder: 'hover:border-blue-500', testId: 'action-backlog' },
  { icon: 'smart_toy', label: 'Open Agents', color: 'text-purple-400', hoverBorder: 'hover:border-purple-500', testId: 'action-agents' },
  { icon: 'add_circle', label: 'Add a needle', color: 'text-green-400', hoverBorder: 'hover:border-green-500', testId: 'action-add-needle' },
] as const

export default function QuickActionsWidget({ onAddNeedle }: Props) {
  const navigate = useNavigate()

  const handlers: Record<string, () => void> = {
    'Open Backlog': () => navigate('/backlog'),
    'Open Agents': () => navigate('/agents'),
    'Add a needle': onAddNeedle,
  }

  return (
    <Card padding="sm" className="sm:p-6">
      <h2 className="text-lg font-semibold mb-4">Quick actions</h2>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {ACTIONS.map(({ icon, label, color, hoverBorder, testId }) => (
          <button
            key={label}
            data-testid={testId}
            onClick={handlers[label]}
            className={`flex items-center gap-2 px-3 py-2.5 min-h-[44px] bg-slate-900 rounded-lg border border-slate-800 ${hoverBorder} transition-colors text-sm text-slate-300`}
          >
            <Icon name={icon} className={color} size={18} />
            {label}
          </button>
        ))}
      </div>
    </Card>
  )
}
