import { useState, useEffect } from 'react'
import { api } from '../lib/api'

interface ReviewCheck {
  name: string
  passed: boolean
  detail: string
  required: boolean
}

interface ReviewData {
  spec_path: string
  readiness: {
    ready: boolean
    file_path: string | null
    checks: ReviewCheck[]
  }
  drift: {
    drift: boolean
    items: { kind: string; detail: string }[]
    summary: string
  }
  constitution: {
    principles: { source: string; section: string; text: string }[]
    violations: { principle: string; detail: string }[]
  }
}

export function SpecReview({ specPath }: { specPath: string }) {
  const [data, setData] = useState<ReviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .get<ReviewData>(`/specs/${specPath}/review`)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Could not load review')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [specPath])

  if (loading) {
    return (
      <div className="text-sm text-slate-400 py-2">Loading review...</div>
    )
  }

  if (error || !data) {
    return (
      <div
        data-testid="review-error"
        className="text-sm text-red-400 py-2"
      >
        {error ?? 'Review unavailable'}
      </div>
    )
  }

  const { readiness, drift, constitution } = data
  const failingRequired = readiness.checks.filter((c) => !c.passed && c.required)

  return (
    <div className="space-y-3 text-sm">
      <div
        data-testid="review-readiness"
        className={`flex items-center gap-2 ${readiness.ready ? 'text-green-400' : 'text-amber-400'}`}
      >
        <span className="font-medium">
          {readiness.ready
            ? 'Ready to build'
            : `${failingRequired.length} required item${failingRequired.length === 1 ? '' : 's'} left`}
        </span>
      </div>

      <div data-testid="review-drift" className="space-y-1">
        {drift.drift ? (
          <ul className="space-y-1">
            {drift.items.map((item, i) => (
              <li key={i} className="text-slate-400 text-xs">
                {item.detail}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-slate-500 text-xs">No drift detected</span>
        )}
      </div>

      <div data-testid="review-constitution" className="space-y-1">
        <span className="text-slate-400 text-xs">
          Inherits {constitution.principles.length} project principle{constitution.principles.length === 1 ? '' : 's'}
        </span>
        {constitution.violations.length > 0 && (
          <ul className="space-y-1 mt-1">
            {constitution.violations.map((v, i) => (
              <li key={i} className="text-red-400 text-xs">
                {v.detail}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
