import { useState, useEffect } from 'react'
import { api } from '../lib/api'

interface Verb {
  name: string
  description: string
}

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_-]*$/

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
