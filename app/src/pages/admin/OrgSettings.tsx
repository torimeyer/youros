import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface OrgSettingsData {
  org_name: string
  logo_url: string
  marketplace_url: string
  welcome_message: string
  default_skill_pack_id: string
}

interface PoliciesSummary {
  max_agent_budget: number | null
  require_approval_above: number | null
  allowed_models: string[]
  audit_retention_days: number | null
  isolation_level: string
}

export default function OrgSettings() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [settings, setSettings] = useState<OrgSettingsData>({
    org_name: '',
    logo_url: '',
    marketplace_url: '',
    welcome_message: '',
    default_skill_pack_id: '',
  })
  const [policies, setPolicies] = useState<PoliciesSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.get<{ settings: OrgSettingsData; policies_summary: PoliciesSummary }>('/org/settings')
      .then((res) => {
        setSettings(res.settings)
        setPolicies(res.policies_summary)
        setError('')
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Could not load org settings')
      })
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      const res = await api.patch<{ settings: OrgSettingsData }>('/org/settings', settings)
      setSettings(res.settings)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  const card = darkMode
    ? 'bg-gray-800 border border-gray-700 rounded-xl p-6 mb-4'
    : 'bg-white border border-gray-200 rounded-xl p-6 mb-4'

  const label = darkMode ? 'block text-sm text-gray-300 mb-1' : 'block text-sm text-gray-600 mb-1'

  const input = darkMode
    ? 'w-full rounded-lg border border-gray-600 bg-gray-700 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'
    : 'w-full rounded-lg border border-gray-300 bg-white text-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

  const textarea = `${input} resize-none`

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className={darkMode ? 'text-gray-400 text-sm' : 'text-gray-600 text-sm'}>
          Loading settings...
        </span>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-8 px-4">
      <h1 className={`text-xl font-semibold mb-6 ${darkMode ? 'text-white' : 'text-gray-900'}`}>
        Org Settings
      </h1>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 text-red-700 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <form onSubmit={handleSave}>
        {/* Branding */}
        <div className={card}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="business" className={darkMode ? 'text-blue-400' : 'text-blue-600'} size={20} />
            <h2 className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>Branding</h2>
          </div>
          <div className="space-y-4">
            <div>
              <label className={label} htmlFor="org_name">
                Display name
              </label>
              <input
                id="org_name"
                type="text"
                className={input}
                value={settings.org_name}
                onChange={(e) => setSettings({ ...settings, org_name: e.target.value })}
                placeholder="Your company name"
              />
            </div>
            <div>
              <label className={label} htmlFor="logo_url">
                Logo URL
              </label>
              <input
                id="logo_url"
                type="url"
                className={input}
                value={settings.logo_url}
                onChange={(e) => setSettings({ ...settings, logo_url: e.target.value })}
                placeholder="https://example.com/logo.png"
              />
            </div>
          </div>
        </div>

        {/* Marketplace */}
        <div className={card}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="storefront" className={darkMode ? 'text-purple-400' : 'text-purple-600'} size={20} />
            <h2 className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>Marketplace</h2>
          </div>
          <div>
            <label className={label} htmlFor="marketplace_url">
              Marketplace URL
            </label>
            <input
              id="marketplace_url"
              type="url"
              className={input}
              value={settings.marketplace_url}
              onChange={(e) => setSettings({ ...settings, marketplace_url: e.target.value })}
              placeholder="https://marketplace.yourcompany.com"
            />
            <p className={`mt-1 text-xs ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
              Link your team to an internal or external skill marketplace.
            </p>
          </div>
        </div>

        {/* Welcome message */}
        <div className={card}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="waving_hand" className={darkMode ? 'text-yellow-400' : 'text-yellow-600'} size={20} />
            <h2 className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>Welcome message</h2>
          </div>
          <div>
            <label className={label} htmlFor="welcome_message">
              Message shown to new members when they join
            </label>
            <textarea
              id="welcome_message"
              rows={4}
              className={textarea}
              value={settings.welcome_message}
              onChange={(e) => setSettings({ ...settings, welcome_message: e.target.value })}
              placeholder="Welcome to our AI workspace! Here's what you need to know to get started..."
            />
          </div>
        </div>

        {/* Default starter pack */}
        <div className={card}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="rocket_launch" className={darkMode ? 'text-green-400' : 'text-green-600'} size={20} />
            <h2 className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>Default starter pack</h2>
          </div>
          <div>
            <label className={label} htmlFor="default_skill_pack_id">
              Starter pack ID
            </label>
            <input
              id="default_skill_pack_id"
              type="text"
              className={input}
              value={settings.default_skill_pack_id}
              onChange={(e) => setSettings({ ...settings, default_skill_pack_id: e.target.value })}
              placeholder="Leave blank to use the community default"
            />
            <p className={`mt-1 text-xs ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
              New members receive this skill pack during onboarding.
            </p>
          </div>
        </div>

        {/* Policies summary (read-only) */}
        {policies && (
          <div className={card}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Icon name="policy" className={darkMode ? 'text-orange-400' : 'text-orange-600'} size={20} />
                <h2 className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>Active policies</h2>
              </div>
              <a
                href="/admin/policies"
                className={`text-xs ${darkMode ? 'text-blue-400 hover:text-blue-300' : 'text-blue-600 hover:text-blue-700'}`}
              >
                Edit policies →
              </a>
            </div>
            <div className={`space-y-2 text-sm ${darkMode ? 'text-gray-300' : 'text-gray-600'}`}>
              {policies.max_agent_budget != null && (
                <div className="flex justify-between">
                  <span>Max spend per agent</span>
                  <span className="font-medium">${policies.max_agent_budget}</span>
                </div>
              )}
              {policies.require_approval_above != null && (
                <div className="flex justify-between">
                  <span>Approval required above</span>
                  <span className="font-medium">${policies.require_approval_above}</span>
                </div>
              )}
              {policies.audit_retention_days != null && (
                <div className="flex justify-between">
                  <span>Audit log kept for</span>
                  <span className="font-medium">{policies.audit_retention_days} days</span>
                </div>
              )}
              {policies.allowed_models.length > 0 && (
                <div className="flex justify-between">
                  <span>Allowed models</span>
                  <span className="font-medium">{policies.allowed_models.join(', ')}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span>Agent isolation</span>
                <span className="font-medium capitalize">{policies.isolation_level}</span>
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-3 mt-2">
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-5 py-2 text-sm font-medium transition-colors"
          >
            {saving ? 'Saving...' : 'Save changes'}
          </button>
          {saved && (
            <span className={`text-sm ${darkMode ? 'text-green-400' : 'text-green-600'}`}>
              Saved.
            </span>
          )}
        </div>
      </form>
    </div>
  )
}
