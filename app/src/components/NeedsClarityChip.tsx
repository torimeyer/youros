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
  onResolved?: () => void
}

const CHECK_LABEL: Record<string, string> = {
  plan_path_present: 'Spec file linked',
  file_exists: 'Spec file exists',
  has_ac_checkboxes: 'Has acceptance criteria',
  no_vague_ac: 'No vague acceptance criteria',
  has_file_paths: 'References real files',
  ac_count_threshold: 'Enough acceptance criteria (≥3)',
  referenced_files_exist: 'Referenced files exist',
  in_repo_scope: 'In-repo scope',
  is_unblocked: 'No blockers',
  outcome_concrete: 'Clear outcome',
}

const CHECK_PLACEHOLDER: Record<string, string> = {
  has_ac_checkboxes: 'Add acceptance criteria — one per line: - [ ] When X, the result is Y',
  no_vague_ac: 'Rewrite vague lines to be specific and testable. Remove: TBD, ?, TODO, maybe, discuss',
  has_file_paths: 'List the files this spec will touch: api/routers/foo.py, app/src/components/Bar.tsx',
  referenced_files_exist: 'Fix broken file paths — check spelling or recent renames',
  in_repo_scope: 'Clarify how this work lives in the current repo (not upstream or an external system)',
  outcome_concrete: 'State exactly what will be built or changed — no TBD, no vague qualifiers',
}

// Checks dropped from the task rubric by Clarity-1; filter from render to be safe
const TASK_MODE_HIDDEN = new Set(['plan_path_present', 'file_exists', 'has_ac_checkboxes', 'ac_count_threshold'])

export function NeedsClarityChip({
  checks,
  specPath,
  taskId,
  mode = 'spec',
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

  const allChecks = liveChecks.length > 0 ? liveChecks : (checks ?? [])
  if (allChecks.length === 0) return null

  const visibleChecks =
    mode === 'task' ? allChecks.filter((c) => !TASK_MODE_HIDDEN.has(c.name)) : allChecks

  if (visibleChecks.length === 0) return null

  const allPassed = visibleChecks.every((c) => c.passed)
  const checkLines = visibleChecks.map((c) => `${c.passed ? '✓' : '✗'} ${c.name}: ${c.detail}`)

  async function handleSuggest(checkName: string) {
    setSuggesting(checkName)
    try {
      let res: { proposed_fix: string; rationale: string }
      if (mode === 'task' && taskId) {
        res = (await api.post(`/api/tasks/${taskId}/clarify/suggest`, { check: checkName })) as typeof res
      } else if (specPath) {
        res = (await api.post(`/api/specs/${specPath}/clarity/suggest`, { check: checkName })) as typeof res
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
        res = (await api.post(`/api/tasks/${taskId}/clarify/apply`, { check: checkName, fix })) as typeof res
      } else if (specPath) {
        res = (await api.patch(`/api/specs/${specPath}/clarity`, { check: checkName, fix })) as typeof res
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
      <button
        type="button"
        data-testid="needs-clarity-chip"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30 cursor-pointer hover:bg-amber-500/25 transition-colors"
        title={checkLines.join('\n')}
        onClick={(e) => {
          e.stopPropagation()
          setOpen(true)
        }}
      >
        <Icon name="warning" className="text-[10px]" />
        Needs clarity
        <span data-testid="needs-clarity-tooltip" className="sr-only">
          {checkLines.join('; ')}
        </span>
      </button>

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
