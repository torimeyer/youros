import Button from './Button'

export interface ErrorBannerProps {
  message: string
  action?: {
    label: string
    onClick: () => void
  }
}

export default function ErrorBanner({ message, action }: ErrorBannerProps) {
  return (
    <div
      data-testid="error-banner"
      className="p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm flex items-start justify-between gap-4"
    >
      <span>{message}</span>
      {action && (
        <Button
          variant="ghost"
          size="sm"
          onClick={action.onClick}
          className="text-red-400 hover:text-red-300 hover:bg-red-500/10 shrink-0"
        >
          {action.label}
        </Button>
      )}
    </div>
  )
}
