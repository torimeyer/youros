import Icon from '../Icon'
import Button from './Button'

export interface EmptyStateProps {
  icon: string
  title: string
  description?: string
  action?: {
    label: string
    onClick: () => void
  }
}

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div data-testid="empty-state" className="flex flex-col items-center justify-center py-16 gap-4 text-center">
      <div className="p-4 rounded-full bg-slate-100 dark:bg-slate-800">
        <Icon name={icon} size={32} className="text-slate-600 dark:text-slate-400" />
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-base font-semibold text-slate-700 dark:text-slate-300">{title}</p>
        {description && (
          <p className="text-sm text-slate-500 max-w-xs">{description}</p>
        )}
      </div>
      {action && (
        <Button variant="primary" size="md" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  )
}
