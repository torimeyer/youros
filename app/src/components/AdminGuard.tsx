import React from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../stores/app'

export default function AdminGuard({ children }: { children: React.ReactNode }) {
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)

  if (!enterpriseUser || enterpriseUser.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] gap-4 text-center px-6">
        <p className="text-lg font-medium text-slate-700 dark:text-slate-200">
          You don&apos;t have access to this page.
        </p>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          This area is only available to admins.
        </p>
        <Link
          to="/"
          className="text-sm text-indigo-600 dark:text-indigo-400 hover:underline"
        >
          Go back home
        </Link>
      </div>
    )
  }

  return <>{children}</>
}
