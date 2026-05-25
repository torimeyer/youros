import React from 'react'

export interface TextFieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string
  error?: string
  help?: string
  size?: 'sm' | 'md' | 'lg'
}

const sizeClasses: Record<NonNullable<TextFieldProps['size']>, string> = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-3 py-2 text-sm',
  lg: 'px-4 py-3 text-base',
}

export default function TextField({
  label,
  error,
  help,
  size = 'md',
  className = '',
  id,
  ...rest
}: TextFieldProps) {
  const inputId = id ?? (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined)

  const inputClasses = [
    'w-full bg-slate-100 dark:bg-slate-800 border rounded-lg text-slate-800 dark:text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1',
    error
      ? 'border-red-500/50 focus:ring-red-500/50 focus:border-red-500/50'
      : 'border-slate-200 dark:border-slate-700 focus:ring-blue-500/50 focus:border-blue-500/50',
    sizeClasses[size],
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div data-testid="text-field" className="flex flex-col gap-1">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium text-slate-700 dark:text-slate-300">
          {label}
        </label>
      )}
      <input id={inputId} className={inputClasses} {...rest} />
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
      {help && !error && (
        <p className="text-xs text-slate-500">{help}</p>
      )}
    </div>
  )
}
