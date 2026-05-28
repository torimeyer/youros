import type { ReactNode } from 'react'

interface IconProps {
  name: string
  filled?: boolean
  className?: string
  size?: number
}

const CUSTOM_ICONS: Record<string, ReactNode> = {
  needle: (
    <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none"
         stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
      <line x1="5" y1="19" x2="16.5" y2="7.5" />
      <circle cx="18" cy="6" r="2" />
      <path d="M16.4 4.6 q3.4 -1.8 2.6 2" />
    </svg>
  ),
}

export default function Icon({ name, filled, className = '', size }: IconProps) {
  const style = size ? { fontSize: `${size}px` } : undefined
  if (CUSTOM_ICONS[name]) {
    return (
      <span className={`inline-flex items-center justify-center ${className}`} style={style}>
        {CUSTOM_ICONS[name]}
      </span>
    )
  }
  return (
    <span className={`material-symbols-outlined ${filled ? 'filled' : ''} ${className}`} style={style}>
      {name}
    </span>
  )
}
