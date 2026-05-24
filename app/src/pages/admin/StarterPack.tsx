import { useState, useEffect, useCallback } from 'react'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'
import Icon from '../../components/Icon'

interface StarterPack {
  agentfiles: string[]
  skills: string[]
  suggested_first_runs: Array<{ id: string; title: string; description: string; icon: string }>
}

export default function AdminStarterPack() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [pack, setPack] = useState<StarterPack>({ agentfiles: [], skills: [], suggested_first_runs: [] })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [newAgent, setNewAgent] = useState('')
  const [newSkill, setNewSkill] = useState('')

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-slate-900/50 border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-400' : 'text-slate-500'
  const inputCls = `flex-1 rounded-lg border px-3 py-2 text-sm ${darkMode ? 'bg-slate-800 border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'}`
  const chipCls = `inline-flex items-center gap-1 rounded-full px-3 py-1 text-sm ${darkMode ? 'bg-slate-700 text-slate-200' : 'bg-slate-100 text-slate-700'}`

  const fetchPack = useCallback(async () => {
    try {
      const data = await api.get<StarterPack>('/onboarding/org-pack')
      setPack(data)
    } catch {
      // org may not exist yet; leave defaults
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchPack() }, [fetchPack])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      await api.put('/onboarding/org-pack', pack)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Could not save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const addAgent = () => {
    const trimmed = newAgent.trim()
    if (!trimmed || pack.agentfiles.includes(trimmed)) return
    setPack(p => ({ ...p, agentfiles: [...p.agentfiles, trimmed] }))
    setNewAgent('')
  }

  const removeAgent = (id: string) => {
    setPack(p => ({ ...p, agentfiles: p.agentfiles.filter(a => a !== id) }))
  }

  const addSkill = () => {
    const trimmed = newSkill.trim()
    if (!trimmed || pack.skills.includes(trimmed)) return
    setPack(p => ({ ...p, skills: [...p.skills, trimmed] }))
    setNewSkill('')
  }

  const removeSkill = (id: string) => {
    setPack(p => ({ ...p, skills: p.skills.filter(s => s !== id) }))
  }

  if (loading) return <div className="p-6 text-sm text-slate-500">Loading...</div>

  return (
    <div className="space-y-6 p-6">
      <div>
        <h2 className={`text-xl font-semibold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
          Default starter pack
        </h2>
        <p className={`mt-1 text-sm ${labelCls}`}>
          New members in your org get these agents and skills automatically when they first sign up.
        </p>
      </div>

      <div className={cardCls}>
        <h3 className={`mb-1 text-sm font-medium ${darkMode ? 'text-slate-200' : 'text-slate-800'}`}>Agents</h3>
        <p className={`mb-4 text-xs ${labelCls}`}>These agent types will be pre-installed for new members.</p>
        <div className="mb-3 flex flex-wrap gap-2">
          {pack.agentfiles.map(id => (
            <span key={id} className={chipCls}>
              {id}
              <button
                onClick={() => removeAgent(id)}
                className="ml-1 text-slate-400 hover:text-red-400"
                aria-label={`Remove ${id}`}
              >
                <Icon name="close" size={12} />
              </button>
            </span>
          ))}
          {pack.agentfiles.length === 0 && (
            <span className={`text-xs ${labelCls}`}>No agents added yet.</span>
          )}
        </div>
        <div className="flex gap-2">
          <input
            className={inputCls}
            placeholder="Agent ID, e.g. builtin-builder"
            value={newAgent}
            onChange={e => setNewAgent(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addAgent()}
            aria-label="Add agent"
          />
          <button
            onClick={addAgent}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-700"
          >
            Add
          </button>
        </div>
      </div>

      <div className={cardCls}>
        <h3 className={`mb-1 text-sm font-medium ${darkMode ? 'text-slate-200' : 'text-slate-800'}`}>Skills</h3>
        <p className={`mb-4 text-xs ${labelCls}`}>These skills will be enabled for new members by default.</p>
        <div className="mb-3 flex flex-wrap gap-2">
          {pack.skills.map(id => (
            <span key={id} className={chipCls}>
              {id}
              <button
                onClick={() => removeSkill(id)}
                className="ml-1 text-slate-400 hover:text-red-400"
                aria-label={`Remove ${id}`}
              >
                <Icon name="close" size={12} />
              </button>
            </span>
          ))}
          {pack.skills.length === 0 && (
            <span className={`text-xs ${labelCls}`}>No skills added yet.</span>
          )}
        </div>
        <div className="flex gap-2">
          <input
            className={inputCls}
            placeholder="Skill name, e.g. review"
            value={newSkill}
            onChange={e => setNewSkill(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addSkill()}
            aria-label="Add skill"
          />
          <button
            onClick={addSkill}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-700"
          >
            Add
          </button>
        </div>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save changes'}
        </button>
        {saved && <span className="text-sm text-green-500">Saved!</span>}
      </div>
    </div>
  )
}
