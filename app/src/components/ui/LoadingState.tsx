import Icon from '../Icon'
import { SkeletonLine, SkeletonBlock } from './Skeleton'

export interface LoadingStateProps {
  message?: string
  variant?: 'spinner' | 'skeleton-list' | 'skeleton-card'
}

function SpinnerVariant({ message }: { message?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-slate-600 dark:text-slate-400">
      <Icon name="progress_activity" size={20} className="animate-spin" />
      {message && <span className="text-sm">{message}</span>}
    </div>
  )
}

function SkeletonListVariant() {
  return (
    <div className="flex flex-col gap-3 py-4">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 p-4 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl">
          <SkeletonBlock width="w-8" height="h-8" className="rounded-full shrink-0" />
          <div className="flex flex-col gap-2 flex-1">
            <SkeletonLine width="w-1/3" />
            <SkeletonLine width="w-2/3" />
          </div>
        </div>
      ))}
    </div>
  )
}

function SkeletonCardVariant() {
  return (
    <div className="p-5 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl flex flex-col gap-3">
      <SkeletonLine width="w-1/4" />
      <SkeletonLine width="w-full" />
      <SkeletonLine width="w-3/4" />
      <SkeletonBlock width="w-full" height="h-24" />
    </div>
  )
}

export default function LoadingState({ message, variant = 'spinner' }: LoadingStateProps) {
  return (
    <div data-testid="loading-state">
      {variant === 'spinner' && <SpinnerVariant message={message} />}
      {variant === 'skeleton-list' && <SkeletonListVariant />}
      {variant === 'skeleton-card' && <SkeletonCardVariant />}
    </div>
  )
}
