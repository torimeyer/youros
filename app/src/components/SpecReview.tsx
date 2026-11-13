import React, { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { formatDate } from '../lib/time'
import { NeedsClarityChip } from './NeedsClarityChip'

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
  github_pr?: string
}

interface PrState {
  state: string
  merged_at: string | null
  created_at: string
}

type CritiqueRuntime = 'gemini' | 'claude'

type AuditMatchEntry = string | { path?: string; file?: string; detail?: string }

interface RawCrossCritiqueResponse {
  summary?: string
  critique?: string
  audit_clear?: boolean
  matched_files?: AuditMatchEntry[]
  audit_matches?: AuditMatchEntry[]
  existing_files?: AuditMatchEntry[]
  matches?: AuditMatchEntry[]
  findings?: string[]
}

interface NormalizedCrossCritique {
  summary: string
  auditClear: boolean
  matchedFiles: string[]
  findings: string[]
}

function normalizeAuditEntry(entry: AuditMatchEntry): string {
  if (typeof entry === 'string') return entry
  if (entry && typeof entry === 'object') {
    const file = entry.path || entry.file || ''
    if (file && entry.detail) return `${file}: ${entry.detail}`
    return file || entry.detail || ''
  }
  return ''
}

function normalizeCrossCritique(raw: RawCrossCritiqueResponse): NormalizedCrossCritique {
  const rawList =
    raw.matched_files ?? raw.audit_matches ?? raw.existing_files ?? raw.matches ?? []
  const matchedFiles = Array.isArray(rawList)
    ? rawList.map(normalizeAuditEntry).filter(Boolean)
    : []
  const auditClear =
    typeof raw.audit_clear === 'boolean' ? raw.audit_clear : matchedFiles.length === 0
  return {
    summary: typeof raw.summary === 'string' ? raw.summary : typeof raw.critique === 'string' ? raw.critique : '',
    auditClear,
    matchedFiles,
    findings: Array.isArray(raw.findings) ? raw.findings.filter((f): f is string => typeof f === 'string') : [],
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
  const [critiqueRuntime, setCritiqueRuntime] = useState<CritiqueRuntime>('gemini')
  const [critiqueBusy, setCritiqueBusy] = useState(false)
  const [critiqueResult, setCritiqueResult] = useState<NormalizedCrossCritique | null>(null)
  const [critiqueError, setCritiqueError] = useState<string | null>(null)
  const [prState, setPrState] = useState<PrState | null>(null)
  const [prLink, setPrLink] = useState('')
  const [savingPr, setSavingPr] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    setCritiqueResult(null)
    setCritiqueError(null)
  }, [specPath])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .get<ReviewData>(`/specs/${specPath}/review`)
      .then((res) => {
        if (!cancelled) {
          setData(res)
          const link = res.github_pr ?? ''
          setPrLink(link)
          if (link) {
            fetchPrState(link).then((ps) => { if (!cancelled) setPrState(ps) })
          }
        }
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load review') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [specPath, refreshKey])

  // Parse owner/repo#number or full GitHub PR URL and fetch state.
  // Returns null on any parse or network failure.
  async function fetchPrState(link: string): Promise<PrState | null> {
    try {
      let owner: string, repo: string, number: string
      // Try full URL first: github.com/owner/repo/pull/123
      const urlMatch = link.match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/)
      if (urlMatch) {
        ;[, owner, repo, number] = urlMatch
      } else {
        // Try shorthand: owner/repo#123
        const shortMatch = link.match(/^([^/\s]+)\/([^/\s#]+)#(\d+)$/)
        if (!shortMatch) return null
        ;[, owner, repo, number] = shortMatch
      }
      const pr = await api.get<PrState>(`/github/pr/${owner}/${repo}/${number}`)
      return pr
    } catch {
      return null
    }
  }

  const handleSavePr = async () => {
    setSavingPr(true)
    try {
      await api.patch(`/specs/${specPath}/github-pr`, { github_pr: prLink })
      if (prLink) {
        const ps = await fetchPrState(prLink)
        setPrState(ps)
      } else {
        setPrState(null)
      }
    } catch {
      // Saving is best-effort; badge is informational only.
    } finally {
      setSavingPr(false)
    }
  }

  function renderPrBadge(): React.ReactNode {
    if (!prState) return null
    let text: string
    if (prState.state === 'merged') {
      text = `Merged on ${formatDate(prState.merged_at)}`
    } else if (prState.state === 'in_review') {
      text = 'Being reviewed'
    } else if (prState.state === 'closed') {
      text = 'Closed'
    } else {
      const days = Math.floor((Date.now() - new Date(prState.created_at).getTime()) / 86400000)
      text = `Open for ${days} day${days === 1 ? '' : 's'}`
    }
    return (
      <span
        data-testid="spec-pr-badge"
        className="inline-block text-xs px-2 py-0.5 rounded bg-slate-700/50 text-slate-300"
      >
        {text}
      </span>
    )
  }

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

  const handleCrossCritique = async () => {
    setCritiqueBusy(true)
    setCritiqueError(null)
    setCritiqueResult(null)
    const reviewerModel = critiqueRuntime === 'gemini' ? 'gemini-3.1-pro' : 'claude-opus-5-5'
    try {
      const res = await api.post<RawCrossCritiqueResponse>(
        `/specs/${specPath}/cross-critique`,
        {
          reviewer_runtime: critiqueRuntime,
          reviewer_model: reviewerModel,
        }
      )
      setCritiqueResult(normalizeCrossCritique(res ?? {}))
    } catch (err) {
      setCritiqueError(
        err instanceof Error && err.message ? err.message : 'Cross-model critique could not run.'
      )
    } finally {
      setCritiqueBusy(false)
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
      <div data-testid="spec-pr-section" className="space-y-1.5">
        {renderPrBadge()}
        <div className="flex gap-1.5 items-center">
          <input
            data-testid="spec-pr-input"
            type="text"
            value={prLink}
            onChange={(e) => setPrLink(e.target.value)}
            placeholder="owner/repo#123 or pull request URL"
            className="flex-1 text-xs bg-slate-800/50 border border-slate-700/60 rounded px-2 py-1 text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-500"
          />
          <button
            onClick={handleSavePr}
            disabled={savingPr}
            className="text-xs px-2 py-1 rounded bg-slate-700/50 text-slate-400 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>

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

      <div data-testid="review-clarity">
        <NeedsClarityChip
          checks={checks}
          specPath={specPath}
          onResolved={() => setRefreshKey((k) => k + 1)}
        />
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

      <div data-testid="review-cross-critique" className="space-y-2 pt-2 border-t border-slate-700/50">
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Critique runtime"
            data-testid="cross-critique-runtime-select"
            value={critiqueRuntime}
            onChange={(e) => setCritiqueRuntime(e.target.value as CritiqueRuntime)}
            disabled={critiqueBusy}
            className="text-xs bg-slate-800/70 border border-slate-700/60 rounded px-2 py-1 text-slate-300 focus:outline-none focus:border-slate-500"
          >
            <option value="gemini">Gemini 3.1 Pro</option>
            <option value="claude">Claude Opus 5.5</option>
          </select>
          <button
            type="button"
            data-testid="cross-model-critique-btn"
            disabled={critiqueBusy}
            onClick={handleCrossCritique}
            className="text-xs px-2.5 py-1 rounded-md bg-blue-500/15 text-blue-400 hover:bg-blue-500/25 transition-colors disabled:opacity-50"
          >
            {critiqueBusy ? 'Critiquing...' : 'Cross-Model Spec Critique'}
          </button>
        </div>

        {critiqueError && (
          <p data-testid="cross-critique-error" className="text-xs text-amber-400">
            {critiqueError}
          </p>
        )}

        {critiqueResult && (
          <div className="space-y-2 rounded-md border border-slate-700/60 bg-slate-800/40 p-2.5 text-xs">
            <div
              data-testid="cross-critique-audit-status"
              className={`font-medium ${
                critiqueResult.auditClear ? 'text-emerald-400' : 'text-amber-400'
              }`}
            >
              {critiqueResult.auditClear
                ? 'Pre-design audit clear'
                : 'Pre-design audit match found: mark existing files as Existing (reuse): instead of Create:'}
            </div>

            {critiqueResult.summary && (
              <p data-testid="cross-critique-summary" className="text-slate-300">
                {critiqueResult.summary}
              </p>
            )}

            {critiqueResult.matchedFiles.length > 0 && (
              <div data-testid="cross-critique-audit-matches" className="space-y-1">
                <span className="text-slate-400 font-medium">Matched existing files:</span>
                <ul className="list-disc list-inside space-y-0.5 text-amber-300 font-mono">
                  {critiqueResult.matchedFiles.map((file, idx) => (
                    <li key={idx}>{file}</li>
                  ))}
                </ul>
              </div>
            )}

            {critiqueResult.findings.length > 0 && (
              <div data-testid="cross-critique-findings" className="space-y-1">
                <span className="text-slate-400 font-medium">Actionable findings:</span>
                <ul className="list-disc list-inside space-y-0.5 text-slate-300">
                  {critiqueResult.findings.map((finding, idx) => (
                    <li key={idx}>{finding}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
