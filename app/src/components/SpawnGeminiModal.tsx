import { useState } from 'react'
import { buildSpec } from '../lib/spawn'
import type { ReadinessCheck } from './GeminiReadyChip'

interface SpawnGeminiModalProps {
  path: string
  title: string
  checks?: ReadinessCheck[]
  onClose: () => void
  onSpawned?: (agents: string[]) => void
}

export function SpawnGeminiModal({ path, title, checks, onClose, onSpawned }: SpawnGeminiModalProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSpawn() {
    setLoading(true)
    setError(null)
    try {
      const result = await buildSpec(path, undefined, 'gemini')
      if (result.status === 'ok') {
        onSpawned?.(result.agents)
        onClose()
      } else if (result.status === 'conflict') {
        setError('Lock conflict — another agent is already working on this spec.')
        setLoading(false)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Spawn failed')
      setLoading(false)
    }
  }

  return (
    <div
      data-testid="spawn-gemini-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-md w-full mx-4 shadow-xl">
        <h2 className="text-white text-lg font-semibold mb-1">Spawn Gemini agent</h2>
        <p className="text-slate-400 text-sm mb-4">{title}</p>

        {checks && checks.filter((c) => c.passed).length > 0 && (
          <div className="mb-4 space-y-1">
            {checks
              .filter((c) => c.passed)
              .slice(0, 3)
              .map((c) => (
                <div key={c.name} className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="text-green-400">✓</span>
                  {c.detail}
                </div>
              ))}
          </div>
        )}

        {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

        <div className="flex gap-3 justify-end">
          <button
            data-testid="cancel-gemini-btn"
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-400 hover:text-white rounded-lg border border-slate-700 hover:border-slate-500 transition-colors"
          >
            Cancel
          </button>
          <button
            data-testid="spawn-gemini-btn"
            onClick={handleSpawn}
            disabled={loading}
            className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg disabled:opacity-50 transition-colors"
          >
            {loading ? 'Spawning…' : 'Spawn Gemini agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
