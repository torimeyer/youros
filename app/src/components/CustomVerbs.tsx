import { useState, useEffect } from 'react'
import { api } from '../lib/api'

interface Verb {
  name: string
  description: string
}

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_-]*$/

const SUGGESTED_VERBS: Verb[] = [
  { name: 'standup', description: 'Write a daily standup update from recent activity' },
  { name: 'summarize', description: 'Summarize what happened in the last session or day' },
  { name: 'brainstorm', description: 'Generate ideas for a feature or problem' },
  { name: 'debug', description: 'Diagnose and explain a bug or error' },
  { name: 'release', description: 'Draft a release checklist or release notes' },
]

export default function CustomVerbs() {
  const [verbs, setVerbs] = useState<Verb[]>([])
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  const fetchVerbs = async () => {
    try {
      const data = await api.get<{ verbs: Verb[] }>('/ostk/language/verbs')
      setVerbs(data.verbs ?? [])
    } catch {
      setVerbs([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchVerbs() }, [])

  const handleAdd = async () => {
    const trimmedName = newName.trim()
    if (!NAME_RE.test(trimmedName)) {
      setNameError('Name can only contain letters, numbers, hyphens, and underscores')
      return
    }
    setNameError(null)
    setAdding(true)
    try {
      await api.post('/ostk/language/verb', { name: trimmedName, description: newDesc.trim() })
      setNewName('')
      setNewDesc('')
      await fetchVerbs()
    } catch {
      // ignore — list stays unchanged
    } finally {
      setAdding(false)
    }
  }

  const handleAdopt = async (verb: Verb) => {
    try {
      await api.post('/ostk/language/verb', { name: verb.name, description: verb.description })
      await fetchVerbs()
    } catch {
      // ignore
    }
  }

  const handleRemove = async (name: string) => {
    setRemoving(name)
    try {
      await api.delete(`/ostk/language/verb/${name}`)
      await fetchVerbs()
    } catch {
      // ignore
    } finally {
      setRemoving(null)
    }
  }

  const handleNameChange = (v: string) => {
    setNewName(v)
    if (nameError) setNameError(null)
  }

  return (
    <div data-testid="custom-verbs">
      <h2 className="text-base font-semibold mb-4">Custom commands</h2>

      {loading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : verbs.length === 0 ? (
        <p className="text-sm text-slate-500" data-testid="custom-verbs-empty">No custom commands yet.</p>
      ) : (
        <ul className="space-y-2 mb-4" data-testid="custom-verbs-list">
          {verbs.map((verb) => (
            <li
              key={verb.name}
              data-testid={`verb-row-${verb.name}`}
              className="flex items-start gap-3 px-3 py-2.5 bg-slate-800/50 rounded-lg border border-slate-700/50"
            >
              <code className="text-sm font-mono text-accent-400 flex-shrink-0 pt-px">{verb.name}</code>
              <span className="flex-1 text-sm text-slate-300">{verb.description}</span>
              <button
                data-testid={`remove-verb-${verb.name}`}
                onClick={() => handleRemove(verb.name)}
                disabled={removing === verb.name}
                aria-label={`Remove ${verb.name}`}
                className="flex-shrink-0 text-slate-500 hover:text-red-400 transition-colors disabled:opacity-40 text-base leading-none"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {(() => {
        const existingNames = new Set(verbs.map((v) => v.name))
        const available = SUGGESTED_VERBS.filter((s) => !existingNames.has(s.name))
        if (available.length === 0) return null
        return (
          <div data-testid="suggested-commands" className="mb-5">
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">Suggested commands</p>
            <ul className="space-y-1.5">
              {available.map((s) => (
                <li
                  key={s.name}
                  className="flex items-center gap-3 px-3 py-2 bg-slate-800/30 rounded-lg border border-slate-700/30"
                >
                  <code className="text-sm font-mono text-accent-400 shrink-0">{s.name}</code>
                  <span className="flex-1 text-xs text-slate-400">{s.description}</span>
                  <button
                    data-testid={`adopt-${s.name}`}
                    onClick={() => handleAdopt(s)}
                    className="shrink-0 text-xs px-2.5 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
                  >
                    Add
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )
      })()}

      <div className="space-y-2" data-testid="custom-verbs-form">
        <div className="flex gap-2">
          <div className="flex-1">
            <input
              data-testid="verb-name-input"
              type="text"
              value={newName}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="Command name"
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-500"
            />
            {nameError && (
              <p data-testid="verb-name-error" className="text-xs text-red-400 mt-1">{nameError}</p>
            )}
          </div>
          <input
            data-testid="verb-desc-input"
            type="text"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            placeholder="What it does"
            className="flex-1 px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-500"
          />
          <button
            data-testid="verb-add-btn"
            onClick={handleAdd}
            disabled={adding || !newName.trim()}
            className="px-4 py-2 bg-accent-600 hover:bg-accent-700 disabled:opacity-40 rounded-lg text-sm font-medium text-white transition-colors"
          >
            Add
          </button>
        </div>
      </div>
    </div>
  )
}
