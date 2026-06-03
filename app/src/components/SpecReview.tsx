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
    acked: boolean
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
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [freshResult, setFreshResult] = useState<{ ok: boolean; summary: string } | null>(null)
  const [freshBusy, setFreshBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .get<ReviewData>(`/specs/${specPath}/review`)
      .then((res) => { if (!cancelled) setData(res) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load review') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [specPath])

  const handleReconcile = async () => {
    setActionBusy(true)
    setActionMsg(null)
    try {
      const res = await api.post<{ drift: ReviewData['drift']; reconciled: boolean }>(
        `/specs/${specPath}/drift/reconcile`,
        {}
      )
      setData((prev) => prev ? { ...prev, drift: res.drift } : prev)
      setActionMsg(res.reconciled ? 'Spec updated.' : 'Already up to date.')
    } catch {
      setActionMsg('Could not update.')
    } finally {
      setActionBusy(false)
    }
  }

  const handleAck = async () => {
    setActionBusy(true)
    setActionMsg(null)
    try {
      const res = await api.post<{ acked: boolean; drift: ReviewData['drift'] }>(
        `/specs/${specPath}/drift/ack`,
        {}
      )
      setData((prev) => prev ? { ...prev, drift: res.drift } : prev)
      setActionMsg('Got it. Warning hidden.')
    } catch {
      setActionMsg('Could not save.')
    } finally {
      setActionBusy(false)
    }
  }

  const handleFreshVerify = async () => {
    setFreshBusy(true)
    setFreshResult(null)
    try {
      const res = await api.post<{ fresh: boolean; ok: boolean; summary: string }>(
        `/specs/${specPath}/verify?fresh=true`,
        {}
      )
      setFreshResult({ ok: res.ok, summary: res.summary })
    } catch {
      setFreshResult({ ok: false, summary: 'Fresh review could not run.' })
    } finally {
      setFreshBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="text-sm text-slate-400 py-2">Loading review...</div>
    )
  }

  if (error || !data || !data.readiness) {
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
  const checks = readiness.checks ?? []
  const failingRequired = checks.filter((c) => !c.passed && c.required)
  const driftItems = drift?.items ?? []
  const principles = constitution?.principles ?? []
  const violations = constitution?.violations ?? []
  const showDriftActions = drift?.drift && !drift?.acked

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

      <div data-testid="review-drift" className="space-y-2">
        {drift?.drift ? (
          <>
            <ul className="space-y-1">
              {driftItems.map((item, i) => (
                <li key={i} className="text-slate-400 text-xs">
                  {item.detail}
                </li>
              ))}
            </ul>
            {showDriftActions && (
              <div className="flex flex-wrap gap-2 pt-1" data-testid="drift-actions">
                <button
                  data-testid="drift-reconcile-btn"
                  disabled={actionBusy}
                  onClick={handleReconcile}
                  className="text-xs px-2.5 py-1 rounded-md bg-blue-500/15 text-blue-400 hover:bg-blue-500/25 transition-colors disabled:opacity-50"
                >
                  Update spec to match code
                </button>
                <button
                  data-testid="drift-ack-btn"
                  disabled={actionBusy}
                  onClick={handleAck}
                  className="text-xs px-2.5 py-1 rounded-md bg-slate-500/15 text-slate-400 hover:bg-slate-500/25 transition-colors disabled:opacity-50"
                >
                  Keep as is
                </button>
              </div>
            )}
            {actionMsg && (
              <p className="text-xs text-slate-500" data-testid="drift-action-msg">{actionMsg}</p>
            )}
          </>
        ) : (
          <span className="text-slate-500 text-xs">No drift detected</span>
        )}
      </div>

      <div data-testid="review-constitution" className="space-y-1">
        <span className="text-slate-400 text-xs">
          Inherits {principles.length} project principle{principles.length === 1 ? '' : 's'}
        </span>
        {violations.length > 0 && (
          <ul className="space-y-1 mt-1">
            {violations.map((v, i) => (
              <li key={i} className="text-red-400 text-xs">
                {v.detail}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div data-testid="review-fresh-verify" className="space-y-1 pt-1 border-t border-slate-700/50">
        <button
          data-testid="fresh-verify-btn"
          disabled={freshBusy}
          onClick={handleFreshVerify}
          className="text-xs px-2.5 py-1 rounded-md bg-slate-500/15 text-slate-400 hover:bg-slate-500/25 transition-colors disabled:opacity-50"
        >
          {freshBusy ? 'Checking...' : 'Fresh eyes'}
        </button>
        {freshResult && (
          <p
            data-testid="fresh-verify-result"
            className={`text-xs ${freshResult.ok ? 'text-slate-400' : 'text-amber-400'}`}
          >
            {freshResult.summary}
          </p>
        )}
      </div>
    </div>
  )
}
