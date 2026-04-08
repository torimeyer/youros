import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { api } from '../lib/api'

interface GmailMessage {
  id: string
  thread_id: string
  subject: string
  from_name: string
  from_email: string
  snippet: string
  date: string
  is_unread: boolean
}

interface AuthStatus {
  authenticated: boolean
  needs_reauth: boolean
  email: string | null
  unread_count: number
}

interface ConnectAuthUrl {
  url: string
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    if (isToday) {
      return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    }
    const yesterday = new Date(now)
    yesterday.setDate(now.getDate() - 1)
    if (d.toDateString() === yesterday.toDateString()) {
      return 'Yesterday'
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

function gmailUrl(messageId: string): string {
  return `https://mail.google.com/mail/u/0/#inbox/${messageId}`
}

export default function Gmail() {
  const [searchParams] = useSearchParams()
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [messages, setMessages] = useState<GmailMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [lastSynced, setLastSynced] = useState<Date | null>(null)
  const [markingRead, setMarkingRead] = useState<Set<string>>(new Set())

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<AuthStatus>('/gmail/auth/status')
      setAuthStatus(res)
    } catch {
      setAuthStatus({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
    }
  }, [])

  const fetchMessages = useCallback(async () => {
    try {
      const res = await api.get<{ messages: GmailMessage[] }>('/gmail/messages')
      setMessages(res.messages || [])
      setLastSynced(new Date())
    } catch {
      setMessages([])
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    fetchStatus().then(async () => {
      try {
        const status = await api.get<AuthStatus>('/gmail/auth/status')
        if (status.authenticated && !status.needs_reauth) {
          await fetchMessages()
        }
      } catch {
        // ignore
      }
      setLoading(false)
    })
  }, [fetchStatus, fetchMessages])

  // Handle ?connected=true redirect back from OAuth
  useEffect(() => {
    if (searchParams.get('connected') === 'true') {
      fetchStatus().then(() => fetchMessages())
    }
  }, [searchParams, fetchStatus, fetchMessages])

  const handleConnect = async () => {
    try {
      const res = await api.get<ConnectAuthUrl>('/drive/auth/url')
      window.location.href = res.url
    } catch (e: unknown) {
      const err = e as { message?: string }
      alert(err?.message || 'Could not get the sign-in link. Make sure your Google credentials file is set up.')
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    try {
      await api.post('/gmail/sync', {})
      await fetchMessages()
      await fetchStatus()
    } catch {
      // ignore
    } finally {
      setSyncing(false)
    }
  }

  const handleMessageClick = async (msg: GmailMessage) => {
    // Mark as read locally first for instant feedback
    setMessages((prev) =>
      prev.map((m) => (m.id === msg.id ? { ...m, is_unread: false } : m))
    )
    setMarkingRead((prev) => new Set(prev).add(msg.id))

    // Open Gmail in a new tab
    window.open(gmailUrl(msg.id), '_blank', 'noopener,noreferrer')

    // Mark as read on the server
    try {
      await api.post(`/gmail/messages/${msg.id}/read`, {})
    } catch {
      // ignore, UI already updated
    } finally {
      setMarkingRead((prev) => {
        const next = new Set(prev)
        next.delete(msg.id)
        return next
      })
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-20 p-8 flex items-center gap-2 text-slate-400">
          <Icon name="progress_activity" size={20} className="animate-spin" />
          Loading...
        </div>
      </div>
    )
  }

  if (!authStatus?.authenticated || authStatus.needs_reauth) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-20 p-8 max-w-md">
          <div className="bg-slate-900/40 border border-slate-800 p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center mb-4">
              <Icon name="mail" className="text-red-400" size={24} />
            </div>
            {authStatus?.needs_reauth ? (
              <>
                <h2 className="text-xl font-semibold mb-2">Gmail access needs to be updated</h2>
                <p className="text-slate-400 mb-6">
                  Reconnect your Google account to give myOS permission to read your Gmail.
                  This uses the same account you already connected for Drive.
                </p>
                <button
                  onClick={handleConnect}
                  className="w-full py-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
                >
                  Reconnect
                </button>
              </>
            ) : (
              <>
                <h2 className="text-xl font-semibold mb-2">Connect Gmail</h2>
                <p className="text-slate-400 mb-6">
                  See your unread emails without leaving myOS.
                  This uses the same Google account as Drive and Calendar, so no extra credentials are needed.
                </p>
                <button
                  onClick={handleConnect}
                  className="w-full py-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
                >
                  Connect Google account
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Gmail" />
      <div className="pt-20 p-8">
        {/* Header row */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">Gmail</h1>
              {messages.filter((m) => m.is_unread).length > 0 && (
                <span className="px-2 py-0.5 bg-red-500/20 text-red-400 text-sm font-semibold rounded-full">
                  {messages.filter((m) => m.is_unread).length}
                </span>
              )}
            </div>
            {authStatus.email && (
              <p className="text-sm text-slate-400 mt-0.5">{authStatus.email}</p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {lastSynced && (
              <span className="text-xs text-slate-500">
                Synced {lastSynced.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            )}
            <button
              onClick={handleSync}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors disabled:opacity-50"
            >
              <Icon name="sync" size={16} className={syncing ? 'animate-spin' : ''} />
              Sync
            </button>
          </div>
        </div>

        {/* Message list */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="inbox" className="text-red-400" size={18} />
            <h2 className="text-base font-semibold">Unread</h2>
          </div>

          {messages.length === 0 ? (
            <div className="text-center py-8 text-slate-500">
              <Icon name="mark_email_read" size={36} className="mb-2 mx-auto opacity-40" />
              <p>Your inbox is clear.</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-800/60">
              {messages.map((msg) => (
                <button
                  key={msg.id}
                  onClick={() => handleMessageClick(msg)}
                  disabled={markingRead.has(msg.id)}
                  className={`w-full text-left px-3 py-3 transition-colors hover:bg-slate-800/40 first:rounded-t-lg last:rounded-b-lg disabled:opacity-50 ${
                    msg.is_unread ? '' : 'opacity-60'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    {/* Unread dot */}
                    <div className="mt-1.5 shrink-0">
                      {msg.is_unread ? (
                        <span className="w-2 h-2 rounded-full bg-blue-400 block" />
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-transparent block" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <p className={`text-sm truncate ${msg.is_unread ? 'font-semibold text-white' : 'text-slate-300'}`}>
                          {msg.from_name || msg.from_email}
                        </p>
                        <span className="text-xs text-slate-500 shrink-0">{formatDate(msg.date)}</span>
                      </div>
                      <p className={`text-sm truncate mt-0.5 ${msg.is_unread ? 'text-slate-200' : 'text-slate-400'}`}>
                        {msg.subject}
                      </p>
                      <p className="text-xs text-slate-500 truncate mt-0.5">
                        {msg.snippet}
                      </p>
                    </div>
                    <div className="shrink-0 mt-1">
                      <Icon name="open_in_new" size={14} className="text-slate-600" />
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
