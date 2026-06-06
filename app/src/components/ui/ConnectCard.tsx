import React from 'react'
import ErrorBanner from './ErrorBanner'

export interface ConnectCardProps {
  icon: string
  accentColor: string
  title: string
  description: string
  primaryAction: React.ReactNode
  error?: string
}

export default function ConnectCard({
  icon,
  accentColor,
  title,
  description,
  primaryAction,
  error,
}: ConnectCardProps) {
  return (
    <div
      data-testid="connect-card"
      className="max-w-md mx-auto mt-16 sm:mt-20 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 p-5 sm:p-8 rounded-2xl flex flex-col items-center gap-6 text-center"
    >
      <div
        className="p-4 rounded-2xl"
        style={{ backgroundColor: `${accentColor}1a` }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '32px', color: accentColor }}
        >
          {icon}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        <h2 className="text-xl font-bold text-slate-900 dark:text-white">{title}</h2>
        <p className="text-sm text-slate-600 dark:text-slate-400 max-w-xs">{description}</p>
      </div>
      {error && (
        <div className="w-full">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="w-full">{primaryAction}</div>
    </div>
  )
}
