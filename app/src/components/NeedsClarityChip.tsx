import { useState } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

export interface ReadinessCheck {
  name: string
  passed: boolean
  detail: string
}

interface NeedsClarityChipProps {
  checks?: ReadinessCheck[]
  specPath?: string
  taskId?: string
  mode?: 'task' | 'spec'
  stage?: string
  onResolved?: () => void
}

const CHECK_LABEL: Record<string, string> = {
  plan_path_present: 'Spec file linked',
  file_exists: 'Spec file exists',
  has_ac_checkboxes: 'Steps to verify it\'s done',
  no_vague_ac: 'Steps to verify it\'s done',
  has_file_paths: 'Names the files it touches',
  ac_count_threshold: 'At least 3 steps to verify',
  referenced_files_exist: 'All mentioned files can be found',
  in_repo_scope: 'Work lives in this project',
  is_unblocked: 'No blockers',
  outcome_concrete: 'Clear outcome',
}

const CHECK_PLACEHOLDER: Record<string, string> = {
  has_ac_checkboxes: 'Add steps that prove the work is done — one per line: - [ ] When X happens, the result is Y',
  no_vague_ac: 'Rewrite vague steps to be specific and testable. Remove words like: TBD, ?, TODO, maybe, discuss',
  has_file_paths: 'List the files this work will change: api/routers/foo.py, app/src/components/Bar.tsx',
  referenced_files_exist: 'Some file paths listed here can\'t be found. Check for typos or update paths if files moved.',
  in_repo_scope: 'Describe how this work lives inside the current project, not in another tool or outside system.',
  outcome_concrete: 'Describe exactly what will be built or changed. Avoid "TBD" or vague qualifiers.',
}

function mergeAcChecks(checks: ReadinessCheck[]): ReadinessCheck[] {
  const acIdx = checks.findIndex((c) => c.name === 'has_ac_checkboxes')
  const vagueIdx = checks.findIndex((c) => c.name === 'no_vague_ac')
  if (acIdx === -1 || vagueIdx === -1) return checks

  const ac = checks[acIdx]
  const vague = checks[vagueIdx]
  const passed = ac.passed && vague.passed

  // Surface the most actionable failing reason; when passed, show AC detail
  const detail = !ac.passed ? ac.detail : !vague.passed ? vague.detail : ac.detail
  // Use the primary failing check name so AI suggest targets the right endpoint
  const name = !ac.passed ? 'has_ac_checkboxes' : !vague.passed ? 'no_vague_ac' : 'has_ac_checkboxes'

  const merged: ReadinessCheck = { name, passed, detail }
  const result = [...checks]
  result[acIdx] = merged
  result.splice(vagueIdx > acIdx ? vagueIdx : vagueIdx, 1)
  return result
}

// Checks dropped from the task rubric by Clarity-1; filter from render to be safe
const TASK_MODE_HIDDEN = new Set(['plan_path_present', 'file_exists', 'has_ac_checkboxes', 'ac_count_threshold'])

export function NeedsClarityChip({
  checks,
  specPath,
  taskId,
  mode = 'spec',
  stage,
  onResolved,
}: NeedsClarityChipProps) {
  const [open, setOpen] = useState(false)
  const [liveChecks, setLiveChecks] = useState<ReadinessCheck[]>(checks ?? [])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [suggesting, setSuggesting] = useState<string | null>(null)
  const [suggestErrors, setSuggestErrors] = useState<Record<string, string>>({})
  const [rationales, setRationales] = useState<Record<string, string>>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  const [fillingAll, setFillingAll] = useState(false)
  const [fillAllProgress, setFillAllProgress] = useState<{ done: number; total: number } | null>(null)
  const [showApplyAllConfirm, setShowApplyAllConfirm] = useState(false)

  const allChecks = liveChecks.length > 0 ? liveChecks : (checks ?? [])
  if (allChecks.length === 0) return null

  const visibleChecks =
    mode === 'task'
      ? allChecks.filter((c) => !TASK_MODE_HIDDEN.has(c.name))
      : mergeAcChecks(allChecks)

  if (visibleChecks.length === 0) return null

  const allPassed = visibleChecks.every((c) => c.passed)
  const checkLines = visibleChecks.map((c) => `${c.passed ? '✓' : '✗'} ${CHECK_LABEL[c.name] ?? c.name}: ${c.detail}`)

  const failingChecks = visibleChecks.filter((c) => !c.passed)

  async function handleFillAll() {
    if (fillingAll) return
    const targets = failingChecks
    setFillingAll(true)
    setFillAllProgress({ done: 0, total: targets.length })
    await Promise.all(
      targets.map(async (check) => {
        await handleSuggest(check.name)
        setFillAllProgress((prev) => prev ? { done: prev.done + 1, total: prev.total } : null)
      })
    )
    setFillingAll(false)
    setFillAllProgress(null)
    setShowApplyAllConfirm(true)
  }

  async function handleApplyAll() {
    setShowApplyAllConfirm(false)
    for (const check of failingChecks) {
      if (drafts[check.name]?.trim()) {
        await handleSave(check.name)
      }
    }
  }

  async function handleSuggest(checkName: string) {
    setSuggesting(checkName)
    try {
      let res: { proposed_fix: string; rationale: string }
      if (mode === 'task' && taskId) {
        res = (await api.post(`/tasks/${taskId}/clarify/suggest`, { check: checkName })) as typeof res
      } else if (specPath) {
        res = (await api.post(`/specs/${specPath}/clarity/suggest`, { check: checkName })) as typeof res
      } else {
        return
      }
      setDrafts((d) => ({ ...d, [checkName]: res.proposed_fix }))
      setRationales((r) => ({ ...r, [checkName]: res.rationale }))
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : (err as { detail?: string })?.detail ?? String(err)
      setSuggestErrors((e) => ({ ...e, [checkName]: msg }))
    } finally {
      setSuggesting(null)
    }
  }

  async function handleSave(checkName: string) {
    const fix = drafts[checkName]?.trim()
    if (!fix) return
    setSaving(checkName)
    setSaveError(null)
    try {
      let res: { checks: ReadinessCheck[]; ready: boolean }
      if (mode === 'task' && taskId) {
        res = (await api.post(`/tasks/${taskId}/clarify/apply`, { check: checkName, fix })) as typeof res
      } else if (specPath) {
        res = (await api.patch(`/specs/${specPath}/clarity`, { check: checkName, fix })) as typeof res
      } else {
        return
      }
      setLiveChecks(res.checks)
      if (res.ready) {
        setOpen(false)
        onResolved?.()
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(null)
    }
  }

  if (allPassed) {
    return (
      <span
        data-testid="needs-clarity-chip"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-500/15 text-green-400 border border-green-500/30"
      >
        <Icon name="check_circle" className="text-[10px]" />
        Ready
      </span>
    )
  }

  return (
    <>
      {stage === 'ready' ? (
        <span className="inline-flex items-center gap-1">
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-500/15 text-green-400 border border-green-500/30">
            Ready
          </span>
          <button
            type="button"
            data-testid="needs-clarity-chip"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-400/60 border border-amber-500/20 cursor-pointer hover:bg-amber-500/20 transition-colors"
            title="Optional tips to strengthen this spec"
            onClick={(e) => {
              e.stopPropagation()
              setOpen(true)
            }}
          >
            Enhance
          </button>
        </span>
      ) : (
        <button
          type="button"
          data-testid="needs-clarity-chip"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/40 cursor-pointer hover:bg-amber-500/30 transition-colors"
          title={checkLines.join('\n')}
          onClick={(e) => {
            e.stopPropagation()
            setOpen(true)
          }}
        >
          Needs detail
          <span data-testid="needs-clarity-tooltip" className="sr-only">
            {checkLines.join('; ')}
          </span>
        </button>
      )}

      {open && (
        <div
          data-testid="needs-clarity-modal"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false)
          }}
        >
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-lg w-full mx-4 shadow-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-white text-lg font-semibold">What needs clarity</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-slate-400 hover:text-white"
                aria-label="Close"
              >
                <Icon name="close" />
              </button>
            </div>

            {failingChecks.length >= 2 && (specPath || taskId) && (
              <div className="mb-4" data-testid="fill-all-gaps-container">
                <button
                  type="button"
                  data-testid="fill-all-gaps-btn"
                  disabled={fillingAll}
                  onClick={handleFillAll}
                  className="w-full py-2 px-4 rounded-lg text-sm font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {fillingAll && fillAllProgress
                    ? `Filling ${fillAllProgress.done} of ${fillAllProgress.total}…`
                    : 'Fill all gaps with AI'}
                </button>
                {showApplyAllConfirm && (
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      data-testid="apply-all-btn"
                      onClick={handleApplyAll}
                      className="flex-1 py-1 px-3 rounded text-xs font-medium bg-amber-500/30 text-amber-300 border border-amber-500/40 hover:bg-amber-500/40"
                    >
                      Apply all suggestions
                    </button>
                    <button
                      type="button"
                      data-testid="review-first-btn"
                      onClick={() => setShowApplyAllConfirm(false)}
                      className="py-1 px-3 rounded text-xs font-medium bg-slate-700 text-slate-300 border border-slate-600 hover:bg-slate-600"
                    >
                      Review first
                    </button>
                  </div>
                )}
              </div>
            )}

            <div className="space-y-3">
              {visibleChecks.map((check) => (
                <div
                  key={check.name}
                  className={`rounded-lg p-3 border ${
                    check.passed
                      ? 'border-green-500/20 bg-green-500/5'
                      : 'border-amber-500/20 bg-amber-500/5'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <Icon
                      name={check.passed ? 'check_circle' : 'warning'}
                      className={`text-sm mt-0.5 flex-shrink-0 ${
                        check.passed ? 'text-green-400' : 'text-amber-400'
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <p
                        className={`text-sm font-medium ${
                          check.passed ? 'text-green-300' : 'text-amber-300'
                        }`}
                      >
                        {CHECK_LABEL[check.name] ?? check.name}
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">{check.detail}</p>

                      {!check.passed && (specPath || taskId) && (
                        <div className="mt-2">
                          <div className="mb-1">
                            <button
                              type="button"
                              data-testid={`clarity-suggest-${check.name}`}
                              disabled={suggesting === check.name}
                              onClick={() => handleSuggest(check.name)}
                              className="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-700 text-slate-300 border border-slate-600 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {suggesting === check.name ? 'Thinking…' : 'AI suggest'}
                            </button>
                            {suggestErrors[check.name] && (
                              <p
                                data-testid={`clarity-suggest-error-${check.name}`}
                                className="mt-1 text-[10px] text-red-400"
                              >
                                {suggestErrors[check.name].includes('No Anthropic API key') ? (
                                  <>
                                    No API key configured.{' '}
                                    <a href="/settings" className="underline">
                                      Add one in Settings
                                    </a>{' '}
                                    to use AI suggestions.
                                  </>
                                ) : (
                                  suggestErrors[check.name]
                                )}
                              </p>
                            )}
                          </div>
                          <textarea
                            data-testid={`clarity-input-${check.name}`}
                            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 resize-none focus:outline-none focus:border-amber-500/60"
                            rows={3}
                            placeholder={CHECK_PLACEHOLDER[check.name] ?? 'Provide the missing information…'}
                            value={drafts[check.name] ?? ''}
                            onChange={(e) => {
                              setDrafts((d) => ({ ...d, [check.name]: e.target.value }))
                              if (suggestErrors[check.name]) {
                                setSuggestErrors((e) => ({ ...e, [check.name]: '' }))
                              }
                            }}
                          />
                          {rationales[check.name] && (
                            <p
                              data-testid={`clarity-rationale-${check.name}`}
                              className="mt-1 text-xs text-slate-400 italic"
                            >
                              {rationales[check.name]}
                            </p>
                          )}
                          <button
                            type="button"
                            data-testid={`clarity-save-${check.name}`}
                            disabled={saving === check.name || !drafts[check.name]?.trim()}
                            onClick={() => handleSave(check.name)}
                            className="mt-1 px-3 py-1 rounded-lg text-xs font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving === check.name ? 'Saving…' : 'Accept'}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {saveError && <p className="mt-3 text-xs text-red-400">{saveError}</p>}
          </div>
        </div>
      )}
    </>
  )
}
