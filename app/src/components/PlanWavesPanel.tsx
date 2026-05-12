import { useEffect, useState } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

interface WaveNeedle {
  id: string
  title: string
  priority: string
  scope_hint: string
}

interface Wave {
  wave: number
  needles: WaveNeedle[]
  blocked_by_prior: boolean
}

interface WavesResponse {
  waves: Wave[]
  total_needles: number
}

interface Props {
  open: boolean
  onClose: () => void
}

const PRIORITY_COLORS: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-300 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-300 border border-orange-500/30',
  P2: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
  P3: 'bg-slate-500/20 text-slate-400 border border-slate-600',
}

export default function PlanWavesPanel({ open, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<WavesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    setData(null)
    api.get<WavesResponse>('/tasks/waves')
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not load wave plan'))
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  return (
    <div
      data-testid="plan-waves-panel"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-16"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Icon name="account_tree" className="text-purple-400 text-lg" />
            <h2 className="text-base font-semibold text-slate-100">Plan waves</h2>
            {data && (
              <span className="text-xs text-slate-500 ml-1">
                {data.total_needles} open {data.total_needles === 1 ? 'task' : 'tasks'} across {data.waves.length} {data.waves.length === 1 ? 'wave' : 'waves'}
              </span>
            )}
          </div>
          <button
            data-testid="plan-waves-close"
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Icon name="close" className="text-base" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-4">
          {loading && (
            <div data-testid="plan-waves-loading" className="text-sm text-slate-400 py-8 text-center">
              Working out the waves...
            </div>
          )}

          {error && (
            <div data-testid="plan-waves-error" className="text-sm text-red-400 bg-red-500/10 rounded-lg px-4 py-3">
              {error}
            </div>
          )}

          {data && data.waves.length === 0 && (
            <div className="text-sm text-slate-400 py-8 text-center">
              No open tasks to plan.
            </div>
          )}

          {data && data.waves.map((wave) => (
            <div
              key={wave.wave}
              data-testid={`wave-${wave.wave}`}
              className="rounded-xl border border-slate-800 bg-slate-800/40"
            >
              {/* Wave header */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-purple-500/20 text-purple-300 text-xs font-bold">
                  {wave.wave}
                </span>
                <span className="text-sm font-medium text-slate-200">
                  Wave {wave.wave}
                </span>
                <span className="text-xs text-slate-500">
                  {wave.needles.length} {wave.needles.length === 1 ? 'task' : 'tasks'}
                </span>
                {wave.blocked_by_prior && (
                  <span
                    data-testid={`wave-${wave.wave}-blocked`}
                    className="ml-auto text-xs text-amber-400/80 flex items-center gap-1"
                  >
                    <Icon name="lock" className="text-xs" />
                    Starts after wave {wave.wave - 1} finishes
                  </span>
                )}
              </div>

              {/* Needles */}
              <div className="divide-y divide-slate-800/60">
                {wave.needles.map((needle) => (
                  <div
                    key={needle.id}
                    data-testid={`wave-needle-${needle.id}`}
                    className="flex items-start gap-3 px-4 py-3"
                  >
                    <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded font-mono ${PRIORITY_COLORS[needle.priority] ?? PRIORITY_COLORS.P3}`}>
                      {needle.priority}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 leading-snug">{needle.title}</p>
                      {needle.scope_hint && needle.scope_hint !== 'general' && (
                        <p className="text-xs text-slate-500 mt-0.5 truncate">{needle.scope_hint}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer hint */}
        {data && data.waves.length > 0 && (
          <div className="px-5 py-3 border-t border-slate-800 text-xs text-slate-600">
            Tasks in the same wave can run at the same time. Later waves wait for the previous one to finish.
          </div>
        )}
      </div>
    </div>
  )
}
