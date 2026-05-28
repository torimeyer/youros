interface IconProps {
  name: string
  filled?: boolean
  className?: string
  size?: number
}

export default function Icon({ name, filled, className = '', size }: IconProps) {
  const style = size ? { fontSize: `${size}px` } : undefined
  return (
    <span className={`material-symbols-outlined ${filled ? 'filled' : ''} ${className}`} style={style}>
      {name}
    </span>
  )
}
