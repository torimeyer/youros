import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface Policies {
  max_agent_budget: number
  require_approval_above: number
  allowed_models: string[]
  audit_retention_days: number
  isolation_level: string
}

interface OrgTemplate {
  id: string
  name: string
  description: string
  icon: string
  prompt_template: string
  model: string
  budget: number
}

const ISOLATION_LEVELS = [
  {
    id: 'open',
    label: 'Open',
    icon: 'lock_open',
    description: 'Agents can read, write, and run commands. Full autonomy for trusted teams.',
  },
  {
    id: 'governed',
    label: 'Governed',
    icon: 'shield',
    description: 'Agents can read and search, but need approval for writes and commands.',
  },
  {
    id: 'sealed',
    label: 'Sealed',
    icon: 'lock',
    description: 'Agents can only read files and search. No writes, no commands.',
  },
]

const PROVIDER_POLICY_OPTIONS = [
  {
    value: 'auto',
    label: 'Smart (recommended)',
    description: 'Picks the best AI for each message automatically.',
  },
  {
    value: 'claude_only',
    label: 'Claude only',
    description: 'All messages go to Claude. Gemini is never used.',
  },
  {
    value: 'gemini_only',
    label: 'Gemini only',
    description: 'All messages go to Gemini. Falls back to Claude if Gemini is unavailable.',
  },
  {
    value: 'prefer_gemini',
    label: 'Prefer Gemini for simple tasks',
    description: 'Short, straightforward messages go to Gemini. Longer or code-heavy messages go to Claude.',
  },
]

export default function AdminPolicies() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [policies, setPolicies] = useState<Policies | null>(null)
  const [templates, setTemplates] = useState<OrgTemplate[]>([])
  const [agentfile, setAgentfile] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // Editable fields
  const [maxBudget, setMaxBudget] = useState('')
  const [approvalAbove, setApprovalAbove] = useState('')
  const [auditRetention, setAuditRetention] = useState('')
  const [providerPolicy, setProviderPolicy] = useState('auto')
  const [savingProvider, setSavingProvider] = useState(false)

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-slate-900/50 border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-200' : 'text-slate-800'
  const inputCls = darkMode ? 'bg-slate-800 border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'

  useEffect(() => {
    Promise.all([
      api.get<{ policies: Policies }>('/enterprise/policies'),
      api.get<{ templates: OrgTemplate[] }>('/enterprise/templates'),
      api.get<{ content: string }>('/enterprise/agentfile'),
      api.get<{ settings: { provider_policy?: string } }>('/org/settings').catch(() => ({ settings: {} })),
    ])
      .then(([polRes, tmplRes, afRes, orgRes]) => {
        const p = polRes.policies || polRes as unknown as Policies
        setPolicies(p)
        setMaxBudget(String(p.max_agent_budget))
        setApprovalAbove(String(p.require_approval_above))
        setAuditRetention(String(p.audit_retention_days))
        setTemplates(tmplRes.templates || [])
        setAgentfile(afRes.content || '')
        const s = (orgRes as { settings?: { provider_policy?: string } }).settings || {}
        setProviderPolicy(s.provider_policy || 'auto')
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSetIsolation = async (level: string) => {
    try {
      await api.patch('/enterprise/isolation', { level })
      setPolicies((prev) => prev ? { ...prev, isolation_level: level } : prev)
    } catch {
      // ignore
    }
  }

  const handleSavePolicies = async () => {
    setSaving(true)
    try {
      await api.patch('/enterprise/policies', {
        max_agent_budget: parseFloat(maxBudget) || 10,
        require_approval_above: parseFloat(approvalAbove) || 5,
        audit_retention_days: parseInt(auditRetention) || 90,
      })
    } catch {
      // ignore
    } finally {
      setSaving(false)
    }
  }

  const handleSaveProviderPolicy = async (value: string) => {
    setProviderPolicy(value)
    setSavingProvider(true)
    try {
      await api.patch('/org/settings', { provider_policy: value })
    } catch {
      // ignore
    } finally {
      setSavingProvider(false)
    }
  }

  const handleRegenerateAgentfile = async () => {
    try {
      const res = await api.post<{ content: string }>('/enterprise/agentfile/generate')
      setAgentfile(res.content || '')
    } catch {
      // ignore
    }
  }

  const handleDeleteTemplate = async (id: string) => {
    try {
      await api.delete(`/enterprise/templates/${id}`)
      setTemplates((prev) => prev.filter((t) => t.id !== id))
    } catch {
      // ignore
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Isolation level */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-2 ${headingCls}`}>Agent guardrails</h2>
        <p className={`text-sm mb-4 ${labelCls}`}>
          Controls what agents can do. Applies to everyone on the team.
        </p>
        <div className="space-y-3">
          {ISOLATION_LEVELS.map((level) => {
            const active = policies?.isolation_level === level.id
            return (
              <button
                key={level.id}
                onClick={() => handleSetIsolation(level.id)}
                className={`w-full flex items-start gap-4 p-4 rounded-xl border text-left transition-colors ${
                  active
                    ? 'bg-indigo-500/20 border-indigo-500'
                    : darkMode
                      ? 'bg-slate-900 border-slate-800 hover:border-slate-700'
                      : 'bg-white border-slate-200 hover:border-slate-300'
                }`}
              >
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
                  active ? 'bg-indigo-500/30 text-indigo-300' : darkMode ? 'bg-slate-800 text-slate-400' : 'bg-slate-100 text-slate-500'
                }`}>
                  <Icon name={level.icon} size={22} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className={`font-medium ${valueCls}`}>{level.label}</p>
                  <p className={`text-sm mt-0.5 ${labelCls}`}>{level.description}</p>
                </div>
                {active && <Icon name="check_circle" className="text-indigo-400 mt-1" size={20} />}
              </button>
            )
          })}
        </div>
      </div>

      {/* Provider routing */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-2 ${headingCls}`}>Provider routing</h2>
        <p className={`text-sm mb-4 ${labelCls}`}>
          Controls which AI answers chat messages for your team. Personal chats always use smart routing.
        </p>
        <select
          data-testid="provider-policy-select"
          value={providerPolicy}
          onChange={(e) => handleSaveProviderPolicy(e.target.value)}
          disabled={savingProvider}
          className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 disabled:opacity-50 ${inputCls}`}
        >
          {PROVIDER_POLICY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {providerPolicy && (
          <p className={`text-xs mt-2 ${labelCls}`}>
            {PROVIDER_POLICY_OPTIONS.find((o) => o.value === providerPolicy)?.description}
          </p>
        )}
      </div>

      {/* Budget and approval */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-4 ${headingCls}`}>Budget and approval limits</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
          <div>
            <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Max agent budget ($)</label>
            <input
              type="number"
              value={maxBudget}
              onChange={(e) => setMaxBudget(e.target.value)}
              className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
            />
          </div>
          <div>
            <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Needs approval above ($)</label>
            <input
              type="number"
              value={approvalAbove}
              onChange={(e) => setApprovalAbove(e.target.value)}
              className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
            />
          </div>
          <div>
            <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Audit retention (days)</label>
            <input
              type="number"
              value={auditRetention}
              onChange={(e) => setAuditRetention(e.target.value)}
              className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
            />
          </div>
        </div>
        <button
          onClick={handleSavePolicies}
          disabled={saving}
          className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
        >
          {saving ? 'Saving...' : 'Save policies'}
        </button>
      </div>

      {/* Shared agent templates */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-4 ${headingCls}`}>Shared agent templates</h2>
        <p className={`text-sm mb-4 ${labelCls}`}>
          These templates are available to all team members when creating agents.
        </p>
        {templates.length === 0 ? (
          <p className={`text-sm ${labelCls}`}>No shared templates yet.</p>
        ) : (
          <div className="space-y-2">
            {templates.map((t) => (
              <div key={t.id} className={`flex items-center justify-between px-4 py-3 rounded-lg ${darkMode ? 'bg-slate-800/50' : 'bg-slate-50'}`}>
                <div className="flex items-center gap-3">
                  <Icon name={t.icon || 'smart_toy'} className="text-indigo-400" size={20} />
                  <div>
                    <p className={`text-sm font-medium ${valueCls}`}>{t.name}</p>
                    <p className={`text-xs ${labelCls}`}>{t.description}</p>
                  </div>
                </div>
                <button
                  onClick={() => handleDeleteTemplate(t.id)}
                  className="text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Agentfile */}
      <div className={cardCls}>
        <div className="flex items-center justify-between mb-4">
          <h2 className={`text-base font-semibold ${headingCls}`}>Agentfile</h2>
          <button
            onClick={handleRegenerateAgentfile}
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Regenerate
          </button>
        </div>
        <pre className={`text-xs p-4 rounded-lg overflow-x-auto ${darkMode ? 'bg-slate-800 text-slate-300' : 'bg-slate-50 text-slate-700'}`}>
          {agentfile || 'No Agentfile configured.'}
        </pre>
      </div>
    </div>
  )
}
