export interface SkeletonLineProps {
  width?: string
  className?: string
}

export function SkeletonLine({ width = 'w-full', className = '' }: SkeletonLineProps) {
  return (
    <div
      data-testid="skeleton-line"
      className={`bg-slate-800 animate-pulse rounded h-4 ${width} ${className}`}
    />
  )
}

export interface SkeletonBlockProps {
  width?: string
  height?: string
  className?: string
}

export function SkeletonBlock({ width = 'w-full', height = 'h-24', className = '' }: SkeletonBlockProps) {
  return (
    <div
      data-testid="skeleton-block"
      className={`bg-slate-800 animate-pulse rounded ${width} ${height} ${className}`}
    />
  )
}

export interface SkeletonAvatarProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const avatarSizeClasses: Record<NonNullable<SkeletonAvatarProps['size']>, string> = {
  sm: 'w-6 h-6',
  md: 'w-10 h-10',
  lg: 'w-14 h-14',
}

export function SkeletonAvatar({ size = 'md', className = '' }: SkeletonAvatarProps) {
  return (
    <div
      data-testid="skeleton-avatar"
      className={`bg-slate-800 animate-pulse rounded-full shrink-0 ${avatarSizeClasses[size]} ${className}`}
    />
  )
}
