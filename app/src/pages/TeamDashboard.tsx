import { useState, useEffect } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { api } from '../lib/api'
import { useAppStore } from '../stores/app'

interface Member {
  id: string
  email: string
  role: string
  added_at: string
}

interface SpendEntry {
  email: string
  total_budget: number
  agent_count: number
  chat_count: number
}

interface PendingInvite {
  token: string
  email: string
  role: string
  expires_at: number
}

interface DashboardData {
  members: Member[]
  spend_by_user: SpendEntry[]
  total_members: number
  policies: Record<string, unknown>
  pending_invites: PendingInvite[]
  active_agents?: { name: string; spawned_at: string; user: string }[]
}

export default function TeamDashboard() {
  const darkMode = useAppStore((s) => s.darkMode)
  const enterpriseUser = useAppStore((s) => s.enterpriseUser)

  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // API keys
  const [apiKeyProviders, setApiKeyProviders] = useState<string[]>([])
  const [apiKeyProvider, setApiKeyProvider] = useState('anthropic')
  const [apiKeyValue, setApiKeyValue] = useState('')
  const [apiKeySaving, setApiKeySaving] = useState(false)
  const [apiKeyError, setApiKeyError] = useState('')

  // Invite form
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [inviteUrl, setInviteUrl] = useState('')
  const [inviteError, setInviteError] = useState('')
  const [inviting, setInviting] = useState(false)

  const fetchDashboard = async () => {
    try {
      const res = await api.get<DashboardData>('/team/dashboard')
      setData(res)
      setError('')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load team dashboard'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const fetchApiKeys = async () => {
    try {
      const res = await api.get<{ providers: string[] }>('/enterprise/api-keys')
      setApiKeyProviders(res.providers || [])
    } catch {
      // not in enterprise mode or endpoint unavailable
    }
  }

  const handleAddApiKey = async () => {
    if (!apiKeyValue.trim()) return
    setApiKeySaving(true)
    setApiKeyError('')
    try {
      await api.post('/enterprise/api-keys', {
        provider: apiKeyProvider,
        key: apiKeyValue,
      })
      setApiKeyValue('')
      await fetchApiKeys()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to save API key'
      setApiKeyError(msg)
    } finally {
      setApiKeySaving(false)
    }
  }

  const handleRemoveApiKey = async (provider: string) => {
    try {
      await api.delete(`/enterprise/api-keys/${encodeURIComponent(provider)}`)
      await fetchApiKeys()
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    fetchDashboard()
    fetchApiKeys()
  }, [])

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return
    setInviting(true)
    setInviteError('')
    setInviteUrl('')
    try {
      const res = await api.post<{ invite_url: string; email: string }>(
        '/enterprise/invite',
        { email: inviteEmail, role: inviteRole }
      )
      setInviteUrl(res.invite_url)
      setInviteEmail('')
      fetchDashboard()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create invite'
      setInviteError(msg)
    } finally {
      setInviting(false)
    }
  }

  const cardClass = `rounded-xl border p-6 ${
    darkMode
      ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800'
      : 'bg-white border-slate-200'
  }`

  const labelClass = darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'
  const headingClass = darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'
  const valueClass = darkMode ? 'text-white' : 'text-slate-900'

  if (!enterpriseUser || enterpriseUser.role !== 'admin') {
    return (
      <div className="flex-1 flex flex-col">
        <TopBar title="Team" />
        <div className="flex-1 flex items-center justify-center">
          <p className={labelClass}>
            Team management is only available to admins.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col">
      <TopBar title="Team" />
      <div className="flex-1 overflow-y-auto p-6 max-w-5xl mx-auto w-full space-y-6">
        {loading && (
          <div className="flex items-center justify-center py-12">
            <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
          </div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-600 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        {data && !loading && (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className={cardClass}>
                <p className={`text-xs uppercase font-semibold mb-1 ${labelClass}`}>
                  Team members
                </p>
                <p className={`text-2xl font-bold ${valueClass}`}>
                  {data.total_members}
                </p>
              </div>
              <div className={cardClass}>
                <p className={`text-xs uppercase font-semibold mb-1 ${labelClass}`}>
                  Active agents
                </p>
                <p className={`text-2xl font-bold ${valueClass}`}>
                  {data.active_agents?.length ?? 0}
                </p>
              </div>
              <div className={cardClass}>
                <p className={`text-xs uppercase font-semibold mb-1 ${labelClass}`}>
                  Pending invites
                </p>
                <p className={`text-2xl font-bold ${valueClass}`}>
                  {data.pending_invites?.length ?? 0}
                </p>
              </div>
            </div>

            {/* Member list */}
            <div className={cardClass}>
              <h2 className={`text-base font-semibold mb-4 ${headingClass}`}>
                Members
              </h2>
              {data.members.length === 0 ? (
                <p className={`text-sm ${labelClass}`}>No members yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-700' : 'border-slate-200'}`}>
                        <th className={`text-left py-2 px-3 font-medium ${labelClass}`}>Email</th>
                        <th className={`text-left py-2 px-3 font-medium ${labelClass}`}>Role</th>
                        <th className={`text-left py-2 px-3 font-medium ${labelClass}`}>Joined</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.members.map((m) => (
                        <tr
                          key={m.id}
                          className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-800' : 'border-slate-100'}`}
                        >
                          <td className={`py-2 px-3 ${valueClass}`}>{m.email}</td>
                          <td className="py-2 px-3">
                            <span
                              className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                                m.role === 'admin'
                                  ? 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
                                  : 'bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-400'
                              }`}
                            >
                              {m.role}
                            </span>
                          </td>
                          <td className={`py-2 px-3 ${labelClass}`}>
                            {m.added_at
                              ? new Date(m.added_at).toLocaleDateString()
                              : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Spend per user */}
            <div className={cardClass}>
              <h2 className={`text-base font-semibold mb-4 ${headingClass}`}>
                Usage by person
              </h2>
              {data.spend_by_user.length === 0 ? (
                <p className={`text-sm ${labelClass}`}>No usage data yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-700' : 'border-slate-200'}`}>
                        <th className={`text-left py-2 px-3 font-medium ${labelClass}`}>User</th>
                        <th className={`text-right py-2 px-3 font-medium ${labelClass}`}>Agents used</th>
                        <th className={`text-right py-2 px-3 font-medium ${labelClass}`}>Budget used</th>
                        <th className={`text-right py-2 px-3 font-medium ${labelClass}`}>Chats</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.spend_by_user.map((s) => (
                        <tr
                          key={s.email}
                          className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-800' : 'border-slate-100'}`}
                        >
                          <td className={`py-2 px-3 ${valueClass}`}>{s.email}</td>
                          <td className={`py-2 px-3 text-right ${valueClass}`}>
                            {s.agent_count}
                          </td>
                          <td className={`py-2 px-3 text-right ${valueClass}`}>
                            ${s.total_budget.toFixed(2)}
                          </td>
                          <td className={`py-2 px-3 text-right ${valueClass}`}>
                            {s.chat_count}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Policy summary */}
            <div className={cardClass}>
              <h2 className={`text-base font-semibold mb-4 ${headingClass}`}>
                Team policies
              </h2>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className={`text-sm ${labelClass}`}>Max agent budget</p>
                  <p className={`text-sm font-medium ${valueClass}`}>
                    ${String(data.policies.max_agent_budget ?? 10)}
                  </p>
                </div>
                <div className="flex items-center justify-between">
                  <p className={`text-sm ${labelClass}`}>Needs approval above</p>
                  <p className={`text-sm font-medium ${valueClass}`}>
                    ${String(data.policies.require_approval_above ?? 5)}
                  </p>
                </div>
                <div className="flex items-center justify-between">
                  <p className={`text-sm ${labelClass}`}>Audit log retention</p>
                  <p className={`text-sm font-medium ${valueClass}`}>
                    {String(data.policies.audit_retention_days ?? 90)} days
                  </p>
                </div>
                {typeof data.policies.isolation_level === 'string' && (
                  <div className="flex items-center justify-between">
                    <p className={`text-sm ${labelClass}`}>Isolation level</p>
                    <p className={`text-sm font-medium ${valueClass}`}>
                      {data.policies.isolation_level}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* API Keys */}
            <div className={cardClass}>
              <h2 className={`text-base font-semibold mb-4 ${headingClass}`}>
                API Keys
              </h2>
              <p className={`text-sm mb-4 ${labelClass}`}>
                Set org-level API keys so your team can use AI features without managing their own keys.
              </p>

              {apiKeyProviders.length > 0 && (
                <div className="mb-4 space-y-2">
                  {apiKeyProviders.map((p) => (
                    <div
                      key={p}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg ${
                        darkMode ? 'bg-slate-100 dark:bg-slate-800/50' : 'bg-slate-50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <Icon name="vpn_key" className="text-green-600 dark:text-green-400" size={16} />
                        <span className={`text-sm font-medium ${valueClass}`}>
                          {p.charAt(0).toUpperCase() + p.slice(1)}
                        </span>
                      </div>
                      <button
                        onClick={() => handleRemoveApiKey(p)}
                        className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex gap-2">
                <select
                  value={apiKeyProvider}
                  onChange={(e) => setApiKeyProvider(e.target.value)}
                  className={`border rounded-lg px-2 py-2 text-sm ${
                    darkMode
                      ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white'
                      : 'bg-white border-slate-300 text-slate-900'
                  }`}
                >
                  <option value="anthropic">Anthropic</option>
                  <option value="gemini">Google Gemini</option>
                </select>
                <input
                  type="password"
                  value={apiKeyValue}
                  onChange={(e) => setApiKeyValue(e.target.value)}
                  placeholder="Paste API key"
                  className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500 ${
                    darkMode
                      ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white'
                      : 'bg-white border-slate-300 text-slate-900'
                  }`}
                />
                <button
                  onClick={handleAddApiKey}
                  disabled={apiKeySaving || !apiKeyValue.trim()}
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-semibold text-sm rounded-lg"
                >
                  {apiKeySaving ? 'Saving...' : 'Save'}
                </button>
              </div>

              {apiKeyError && (
                <p className="text-red-600 dark:text-red-400 text-sm mt-2">{apiKeyError}</p>
              )}
            </div>

            {/* Invite section */}
            <div className={cardClass}>
              <h2 className={`text-base font-semibold mb-4 ${headingClass}`}>
                Invite a team member
              </h2>
              <div className="flex gap-2 mb-3">
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="Email address"
                  className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500 ${
                    darkMode
                      ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white'
                      : 'bg-white border-slate-300 text-slate-900'
                  }`}
                />
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className={`border rounded-lg px-2 py-2 text-sm ${
                    darkMode
                      ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white'
                      : 'bg-white border-slate-300 text-slate-900'
                  }`}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
                <button
                  onClick={handleInvite}
                  disabled={inviting || !inviteEmail.trim()}
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-semibold text-sm rounded-lg"
                >
                  {inviting ? 'Sending...' : 'Invite'}
                </button>
              </div>

              {inviteUrl && (
                <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-3 text-sm">
                  <p className="text-green-600 dark:text-green-400 font-medium mb-1">Invite link created</p>
                  <p className={`text-xs break-all ${labelClass}`}>{inviteUrl}</p>
                </div>
              )}

              {inviteError && (
                <p className="text-red-600 dark:text-red-400 text-sm mt-1">{inviteError}</p>
              )}

              {/* Pending invites */}
              {data.pending_invites && data.pending_invites.length > 0 && (
                <div className="mt-4">
                  <h3 className={`text-sm font-semibold mb-2 ${headingClass}`}>
                    Pending invites
                  </h3>
                  <div className="space-y-1">
                    {data.pending_invites.map((inv) => (
                      <div
                        key={inv.token}
                        className={`flex items-center justify-between px-3 py-2 rounded-lg ${
                          darkMode ? 'bg-slate-100 dark:bg-slate-800/50' : 'bg-slate-50'
                        }`}
                      >
                        <span className={`text-sm ${valueClass}`}>
                          {inv.email}
                        </span>
                        <span className={`text-xs ${labelClass}`}>
                          Expires{' '}
                          {new Date(inv.expires_at * 1000).toLocaleDateString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
