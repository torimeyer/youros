import React from 'react'

export interface BreadcrumbItem {
  label: string
  href?: string
}

export interface PageHeaderProps {
  title: string
  subtitle?: string
  primaryAction?: React.ReactNode
  overflowMenu?: React.ReactNode
  breadcrumb?: BreadcrumbItem[]
}

export default function PageHeader({
  title,
  subtitle,
  primaryAction,
  overflowMenu,
  breadcrumb,
}: PageHeaderProps) {
  return (
    <div data-testid="page-header" className="mb-6">
      {breadcrumb && breadcrumb.length > 0 && (
        <nav className="flex items-center gap-1 mb-2 text-sm text-slate-500">
          {breadcrumb.map((item, idx) => (
            <React.Fragment key={idx}>
              {idx > 0 && <span>/</span>}
              {item.href ? (
                <a href={item.href} className="hover:text-slate-700 dark:hover:text-slate-300 transition-colors">
                  {item.label}
                </a>
              ) : (
                <span>{item.label}</span>
              )}
            </React.Fragment>
          ))}
        </nav>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900 dark:text-white">{title}</h1>
          {subtitle && (
            <p className="text-sm text-slate-600 dark:text-slate-400">{subtitle}</p>
          )}
        </div>
        {(primaryAction || overflowMenu) && (
          <div className="flex items-center gap-2 shrink-0">
            {primaryAction}
            {overflowMenu}
          </div>
        )}
      </div>
    </div>
  )
}
