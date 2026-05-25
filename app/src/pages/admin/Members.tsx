import { useState, useEffect, useCallback } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface Member {
  id: string
  email: string
  role: string
  added_at: string
}

interface PendingInvite {
  token: string
  email: string
  role: string
  expires_at: number
}

export default function AdminMembers() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<PendingInvite[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  // Invite form
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [inviteUrl, setInviteUrl] = useState('')
  const [inviteError, setInviteError] = useState('')
  const [inviting, setInviting] = useState(false)

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'
  const inputCls = darkMode ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'

  const fetchData = useCallback(async () => {
    try {
      const [membersRes, invitesRes] = await Promise.all([
        api.get<{ members: Member[] }>('/enterprise/members'),
        api.get<{ invites: PendingInvite[] }>('/enterprise/invites'),
      ])
      setMembers(membersRes.members || [])
      setInvites(invitesRes.invites || [])
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return
    setInviting(true)
    setInviteError('')
    setInviteUrl('')
    try {
      const res = await api.post<{ invite_url: string }>('/enterprise/invite', { email: inviteEmail, role: inviteRole })
      setInviteUrl(res.invite_url)
      setInviteEmail('')
      fetchData()
    } catch (err: unknown) {
      setInviteError(err instanceof Error ? err.message : 'Failed to create invite')
    } finally {
      setInviting(false)
    }
  }

  const handleRemoveMember = async (id: string) => {
    try {
      await api.delete(`/enterprise/members/${id}`)
      fetchData()
    } catch {
      // ignore
    }
  }

  const handleChangeRole = async (id: string, newRole: string) => {
    try {
      await api.patch(`/enterprise/members/${id}/role`, { role: newRole })
      fetchData()
    } catch {
      // ignore
    }
  }

  const filtered = members.filter((m) =>
    m.email.toLowerCase().includes(search.toLowerCase())
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Invite section */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-3 ${headingCls}`}>Invite a team member</h2>
        <div className="flex gap-2 mb-3">
          <input
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleInvite() }}
            placeholder="Email address"
            className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
          />
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value)}
            className={`border rounded-lg px-2 py-2 text-sm ${inputCls}`}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button
            onClick={handleInvite}
            disabled={inviting || !inviteEmail.trim()}
            className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
          >
            {inviting ? 'Sending...' : 'Invite'}
          </button>
        </div>
        {inviteUrl && (
          <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-3 text-sm">
            <p className="text-green-600 dark:text-green-400 font-medium mb-1">Invite link created</p>
            <p className={`text-xs break-all ${labelCls}`}>{inviteUrl}</p>
            <button
              onClick={() => navigator.clipboard.writeText(inviteUrl)}
              className="mt-2 text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300"
            >
              Copy link
            </button>
          </div>
        )}
        {inviteError && <p className="text-red-600 dark:text-red-400 text-sm mt-1">{inviteError}</p>}
      </div>

      {/* Members list */}
      <div className={cardCls}>
        <div className="flex items-center justify-between mb-4">
          <h2 className={`text-base font-semibold ${headingCls}`}>Members ({members.length})</h2>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className={`border rounded-lg px-3 py-1.5 text-sm w-48 focus:outline-none focus:border-indigo-500 ${inputCls}`}
          />
        </div>
        {filtered.length === 0 ? (
          <p className={`text-sm ${labelCls}`}>{search ? 'No matches.' : 'No members yet.'}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-700' : 'border-slate-200'}`}>
                  <th className={`text-left py-2 px-3 font-medium ${labelCls}`}>Email</th>
                  <th className={`text-left py-2 px-3 font-medium ${labelCls}`}>Role</th>
                  <th className={`text-left py-2 px-3 font-medium ${labelCls}`}>Joined</th>
                  <th className={`text-right py-2 px-3 font-medium ${labelCls}`}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => (
                  <tr key={m.id} className={`border-b ${darkMode ? 'border-slate-200 dark:border-slate-800' : 'border-slate-100'}`}>
                    <td className={`py-2 px-3 ${valueCls}`}>{m.email}</td>
                    <td className="py-2 px-3">
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                        m.role === 'admin' ? 'bg-indigo-500/20 text-indigo-600 dark:text-indigo-400' : 'bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-400'
                      }`}>
                        {m.role}
                      </span>
                    </td>
                    <td className={`py-2 px-3 ${labelCls}`}>
                      {m.added_at ? new Date(m.added_at).toLocaleDateString() : '-'}
                    </td>
                    <td className="py-2 px-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => handleChangeRole(m.id, m.role === 'admin' ? 'member' : 'admin')}
                          className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 transition-colors"
                        >
                          {m.role === 'admin' ? 'Make member' : 'Make admin'}
                        </button>
                        <button
                          onClick={() => handleRemoveMember(m.id)}
                          className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pending invites */}
      {invites.length > 0 && (
        <div className={cardCls}>
          <h2 className={`text-base font-semibold mb-4 ${headingCls}`}>Pending invites ({invites.length})</h2>
          <div className="space-y-2">
            {invites.map((inv) => (
              <div key={inv.token} className={`flex items-center justify-between px-3 py-2 rounded-lg ${darkMode ? 'bg-slate-100 dark:bg-slate-800/50' : 'bg-slate-50'}`}>
                <div>
                  <span className={`text-sm ${valueCls}`}>{inv.email}</span>
                  <span className={`text-xs ml-2 ${labelCls}`}>({inv.role})</span>
                </div>
                <span className={`text-xs ${labelCls}`}>
                  Expires {new Date(inv.expires_at * 1000).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
