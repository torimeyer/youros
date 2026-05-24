import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

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

export default function AdminOverview() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Quick invite
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteUrl, setInviteUrl] = useState('')
  const [inviting, setInviting] = useState(false)

  useEffect(() => {
    api.get<DashboardData>('/team/dashboard')
      .then((res) => { setData(res); setError('') })
      .catch((err: unknown) => { setError(err instanceof Error ? err.message : 'Failed to load') })
      .finally(() => setLoading(false))
  }, [])

  const handleQuickInvite = async () => {
    if (!inviteEmail.trim()) return
    setInviting(true)
    try {
      const res = await api.post<{ invite_url: string }>('/enterprise/invite', { email: inviteEmail, role: 'member' })
      setInviteUrl(res.invite_url)
      setInviteEmail('')
    } catch {
      // ignore
    } finally {
      setInviting(false)
    }
  }

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-slate-900/50 border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-200' : 'text-slate-800'
  const inputCls = darkMode ? 'bg-slate-800 border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400 text-sm">
        {error}
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className={cardCls}>
          <p className={`text-xs uppercase font-semibold mb-1 ${labelCls}`}>Team members</p>
          <p className={`text-2xl font-bold ${valueCls}`}>{data.total_members}</p>
        </div>
        <div className={cardCls}>
          <p className={`text-xs uppercase font-semibold mb-1 ${labelCls}`}>Active agents</p>
          <p className={`text-2xl font-bold ${valueCls}`}>{data.active_agents?.length ?? 0}</p>
        </div>
        <div className={cardCls}>
          <p className={`text-xs uppercase font-semibold mb-1 ${labelCls}`}>Pending invites</p>
          <p className={`text-2xl font-bold ${valueCls}`}>{data.pending_invites?.length ?? 0}</p>
        </div>
      </div>

      {/* Quick invite */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-3 ${headingCls}`}>Quick invite</h2>
        <div className="flex gap-2">
          <input
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleQuickInvite() }}
            placeholder="teammate@company.com"
            className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
          />
          <button
            onClick={handleQuickInvite}
            disabled={inviting || !inviteEmail.trim()}
            className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
          >
            {inviting ? 'Sending...' : 'Invite'}
          </button>
        </div>
        {inviteUrl && (
          <div className="mt-3 bg-green-500/10 border border-green-500/30 rounded-lg p-3 text-sm">
            <p className="text-green-400 font-medium mb-1">Invite link created</p>
            <p className={`text-xs break-all ${labelCls}`}>{inviteUrl}</p>
          </div>
        )}
      </div>

      {/* Usage by person */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-4 ${headingCls}`}>Usage by person</h2>
        {data.spend_by_user.length === 0 ? (
          <p className={`text-sm ${labelCls}`}>No usage data yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className={`border-b ${darkMode ? 'border-slate-700' : 'border-slate-200'}`}>
                  <th className={`text-left py-2 px-3 font-medium ${labelCls}`}>User</th>
                  <th className={`text-right py-2 px-3 font-medium ${labelCls}`}>Agents</th>
                  <th className={`text-right py-2 px-3 font-medium ${labelCls}`}>Budget</th>
                  <th className={`text-right py-2 px-3 font-medium ${labelCls}`}>Chats</th>
                </tr>
              </thead>
              <tbody>
                {data.spend_by_user.map((s) => (
                  <tr key={s.email} className={`border-b ${darkMode ? 'border-slate-800' : 'border-slate-100'}`}>
                    <td className={`py-2 px-3 ${valueCls}`}>{s.email}</td>
                    <td className={`py-2 px-3 text-right ${valueCls}`}>{s.agent_count}</td>
                    <td className={`py-2 px-3 text-right ${valueCls}`}>${s.total_budget.toFixed(2)}</td>
                    <td className={`py-2 px-3 text-right ${valueCls}`}>{s.chat_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent team activity */}
      {data.active_agents && data.active_agents.length > 0 && (
        <div className={cardCls}>
          <h2 className={`text-base font-semibold mb-4 ${headingCls}`}>Active agents</h2>
          <div className="space-y-2">
            {data.active_agents.map((a, i) => (
              <div key={i} className={`flex items-center justify-between px-3 py-2 rounded-lg ${darkMode ? 'bg-slate-800/50' : 'bg-slate-50'}`}>
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                  <span className={`text-sm font-medium ${valueCls}`}>{a.name}</span>
                </div>
                <span className={`text-xs ${labelCls}`}>{a.user}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
