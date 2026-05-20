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
}

export function NeedsClarityChip({ checks, specPath, onResolved }: NeedsClarityChipProps) {
  const [open, setOpen] = useState(false)
  const [liveChecks, setLiveChecks] = useState<ReadinessCheck[]>(checks ?? [])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  const allChecks = liveChecks.length > 0 ? liveChecks : (checks ?? [])
  if (allChecks.length === 0) return null

  const allPassed = allChecks.every((c) => c.passed)
  const checkLines = allChecks.map((c) => `${c.passed ? '✓' : '✗'} ${c.name}: ${c.detail}`)

  async function handleSave(checkName: string) {
    if (!specPath) return
    const fix = drafts[checkName]?.trim()
    if (!fix) return
    setSaving(checkName)
    setSaveError(null)
    try {
      const res = await api.patch(`/api/specs/${specPath}/clarity`, { check: checkName, fix }) as { checks: ReadinessCheck[]; ready: boolean }
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
        onClick={(e) => { e.stopPropagation(); setOpen(true) }}
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
          onClick={(e) => { if (e.target === e.currentTarget) setOpen(false) }}
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
              {allChecks.map((check) => (
                <div
                  key={check.name}
                  className={`rounded-lg p-3 border ${check.passed ? 'border-green-500/20 bg-green-500/5' : 'border-amber-500/20 bg-amber-500/5'}`}
                >
                  <div className="flex items-start gap-2">
                    <Icon
                      name={check.passed ? 'check_circle' : 'warning'}
                      className={`text-sm mt-0.5 flex-shrink-0 ${check.passed ? 'text-green-400' : 'text-amber-400'}`}
                    />
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium ${check.passed ? 'text-green-300' : 'text-amber-300'}`}>
                        {CHECK_LABEL[check.name] ?? check.name}
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">{check.detail}</p>

                      {!check.passed && specPath && (
                        <div className="mt-2">
                          <textarea
                            data-testid={`clarity-input-${check.name}`}
                            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 resize-none focus:outline-none focus:border-amber-500/60"
                            rows={3}
                            placeholder="Provide the missing information…"
                            value={drafts[check.name] ?? ''}
                            onChange={(e) => setDrafts((d) => ({ ...d, [check.name]: e.target.value }))}
                          />
                          <button
                            type="button"
                            data-testid={`clarity-save-${check.name}`}
                            disabled={saving === check.name || !drafts[check.name]?.trim()}
                            onClick={() => handleSave(check.name)}
                            className="mt-1 px-3 py-1 rounded-lg text-xs font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving === check.name ? 'Saving…' : 'Save'}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {saveError && (
              <p className="mt-3 text-xs text-red-400">{saveError}</p>
            )}
          </div>
        </div>
      )}
    </>
  )
}
