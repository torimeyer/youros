import React from 'react'

export interface CardProps {
  variant?: 'default' | 'elevated' | 'outlined'
  padding?: 'sm' | 'md' | 'lg'
  hover?: boolean
  className?: string
  children?: React.ReactNode
  onClick?: () => void
}

const variantClasses: Record<NonNullable<CardProps['variant']>, string> = {
  default: 'bg-slate-900/40 border border-slate-800 rounded-xl',
  elevated: 'bg-slate-900 border border-slate-700 rounded-xl shadow-lg',
  outlined: 'bg-transparent border border-slate-700 rounded-xl',
}

const paddingClasses: Record<NonNullable<CardProps['padding']>, string> = {
  sm: 'p-4',
  md: 'p-5',
  lg: 'p-6',
}

export default function Card({
  variant = 'default',
  padding = 'md',
  hover = false,
  className = '',
  children,
  onClick,
}: CardProps) {
  const hoverClass = hover ? 'hover:border-slate-700 transition-colors cursor-pointer' : ''
  const classes = [
    variantClasses[variant],
    paddingClasses[padding],
    hoverClass,
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div data-testid="card" className={classes} onClick={onClick}>
      {children}
    </div>
  )
}
