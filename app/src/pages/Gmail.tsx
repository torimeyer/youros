import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import GmailReplyComposer from '../components/GmailReplyComposer'
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

interface SendCapability {
  has_send_scope: boolean
  reauth_url: string | null
}

type ComposerState = { messageId: string; replyAll: boolean } | null

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
  const [apiNotEnabled, setApiNotEnabled] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [composer, setComposer] = useState<ComposerState>(null)
  const [sendCapability, setSendCapability] = useState<SendCapability | null>(null)
  const [sentConfirmationId, setSentConfirmationId] = useState<string | null>(null)

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
      setApiNotEnabled(false)
    } catch (err: unknown) {
      setMessages([])
      const detail = (err as { response?: { data?: { detail?: { api_not_enabled?: boolean } } } })?.response?.data?.detail
      if (detail?.api_not_enabled) setApiNotEnabled(true)
    }
  }, [])

  const fetchSendCapability = useCallback(async () => {
    try {
      const cap = await api.get<SendCapability>('/gmail/send_capability')
      setSendCapability(cap)
    } catch {
      setSendCapability({ has_send_scope: false, reauth_url: null })
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    ;(async () => {
      try {
        const status = await api.get<AuthStatus>('/gmail/auth/status')
        setAuthStatus(status)
        if (status.authenticated && !status.needs_reauth) {
          await fetchMessages()
          await fetchSendCapability()
        }
      } catch {
        setAuthStatus({ authenticated: false, needs_reauth: false, email: null, unread_count: 0 })
      }
      setLoading(false)
    })()
  }, [fetchMessages, fetchSendCapability])

  // Handle ?connected=true redirect back from OAuth
  useEffect(() => {
    if (searchParams.get('connected') === 'true') {
      fetchStatus().then(() => fetchMessages())
    }
  }, [searchParams, fetchStatus, fetchMessages])

  const handleConnect = async () => {
    setConnectError(null)
    try {
      const res = await api.get<ConnectAuthUrl>('/drive/auth/url/gmail')
      window.location.href = res.url
    } catch {
      setConnectError(
        'Could not get the sign-in link. Make sure the myOS backend is running and your Google credentials file is saved at ~/.myos/google_credentials.json.'
      )
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
    // Toggle inline expand. If already open, collapse.
    const isExpanding = expandedId !== msg.id
    setExpandedId(isExpanding ? msg.id : null)
    if (!isExpanding) {
      setComposer(null)
      return
    }

    // Expanding: mark as read locally first for instant feedback.
    if (msg.is_unread) {
      setMessages((prev) =>
        prev.map((m) => (m.id === msg.id ? { ...m, is_unread: false } : m))
      )
      setMarkingRead((prev) => new Set(prev).add(msg.id))
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
  }

  const handleOpenInGmail = (e: React.MouseEvent, messageId: string) => {
    e.stopPropagation()
    window.open(gmailUrl(messageId), '_blank', 'noopener,noreferrer')
  }

  const handleStartReply = (e: React.MouseEvent, messageId: string, replyAll: boolean) => {
    e.stopPropagation()
    setComposer({ messageId, replyAll })
  }

  const handleReplySent = async (messageId: string) => {
    setComposer(null)
    setSentConfirmationId(messageId)
    setTimeout(() => {
      setSentConfirmationId((current) => (current === messageId ? null : current))
    }, 3000)
    // Refresh the thread list so the new reply shows up.
    try {
      await api.post('/gmail/sync', {})
      await fetchMessages()
    } catch {
      // ignore, confirmation still shows
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
        <div className="pt-20 p-8 max-w-md mx-auto">
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
            {connectError && (
              <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
                {connectError}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (apiNotEnabled) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-20 p-8 max-w-md mx-auto">
          <div className="bg-slate-900/40 border border-amber-800/40 p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-amber-500/20 flex items-center justify-center mb-4">
              <Icon name="warning" className="text-amber-400" size={24} />
            </div>
            <h2 className="text-xl font-semibold mb-2">Gmail API not enabled</h2>
            <p className="text-slate-400 mb-4">
              Your Google Cloud project has the Gmail API disabled. You need to turn it on once. Reconnecting will not fix this.
            </p>
            <a
              href="https://console.cloud.google.com/apis/library/gmail.googleapis.com"
              target="_blank"
              rel="noreferrer"
              className="w-full block text-center py-3 mb-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
            >
              Enable Gmail API in Google Cloud
            </a>
            <p className="text-xs text-slate-500 mb-4">
              After clicking Enable on Google's page, wait 1-2 minutes for the change to propagate, then come back and click Retry.
            </p>
            <button
              onClick={() => { setApiNotEnabled(false); fetchMessages() }}
              className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors"
            >
              Retry
            </button>
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
            <h2 className="text-base font-semibold">Inbox</h2>
          </div>

          {syncing && messages.length === 0 ? (
            <div className="text-center py-8 text-slate-500 flex items-center justify-center gap-2">
              <Icon name="progress_activity" size={20} className="animate-spin" />
              <p>Loading your inbox...</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="text-center py-8 text-slate-500">
              <Icon name="mark_email_read" size={36} className="mb-2 mx-auto opacity-40" />
              <p>No messages in your inbox.</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-800/60">
              {messages.map((msg) => {
                const isExpanded = expandedId === msg.id
                const isComposing = composer?.messageId === msg.id
                const canSend = sendCapability?.has_send_scope === true
                return (
                  <div key={msg.id} className="py-1">
                    <button
                      onClick={() => handleMessageClick(msg)}
                      disabled={markingRead.has(msg.id)}
                      aria-expanded={isExpanded}
                      className={`w-full text-left px-3 py-3 rounded-lg transition-colors hover:bg-slate-800/40 disabled:opacity-50 ${
                        msg.is_unread ? '' : 'opacity-75'
                      }`}
                    >
                      <div className="flex items-start gap-3">
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
                          <Icon
                            name={isExpanded ? 'expand_less' : 'expand_more'}
                            size={18}
                            className="text-slate-500"
                          />
                        </div>
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="px-3 pb-3">
                        <div className="bg-slate-950/40 border border-slate-800 rounded-xl p-4">
                          <div className="flex items-center justify-between gap-2 mb-3">
                            <div className="text-xs text-slate-500">
                              From {msg.from_name || msg.from_email}
                              {msg.from_name ? ` (${msg.from_email})` : ''}
                            </div>
                            <button
                              type="button"
                              onClick={(e) => handleOpenInGmail(e, msg.id)}
                              className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
                            >
                              <Icon name="open_in_new" size={14} />
                              Open in Gmail
                            </button>
                          </div>
                          <p className="text-sm text-slate-200 whitespace-pre-wrap">
                            {msg.snippet}
                          </p>

                          {sentConfirmationId === msg.id && (
                            <div className="mt-3 flex items-center gap-2 text-sm text-emerald-400">
                              <Icon name="check_circle" size={16} />
                              Reply sent
                            </div>
                          )}

                          {!isComposing && sentConfirmationId !== msg.id && (
                            <div className="mt-4 flex items-center gap-2 flex-wrap">
                              {canSend ? (
                                <>
                                  <button
                                    type="button"
                                    onClick={(e) => handleStartReply(e, msg.id, false)}
                                    className="flex items-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm font-medium transition-colors"
                                  >
                                    <Icon name="reply" size={16} />
                                    Reply
                                  </button>
                                  <button
                                    type="button"
                                    onClick={(e) => handleStartReply(e, msg.id, true)}
                                    className="flex items-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm font-medium transition-colors"
                                  >
                                    <Icon name="reply_all" size={16} />
                                    Reply all
                                  </button>
                                </>
                              ) : sendCapability?.reauth_url ? (
                                <a
                                  href={sendCapability.reauth_url}
                                  className="flex items-center gap-1.5 px-3 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors"
                                >
                                  <Icon name="link" size={16} />
                                  Connect Gmail to send replies
                                </a>
                              ) : (
                                <div className="text-xs text-slate-500">
                                  Connect Gmail with send permission to reply from here.
                                </div>
                              )}
                            </div>
                          )}

                          {isComposing && (
                            <GmailReplyComposer
                              threadId={msg.thread_id}
                              inReplyToMessageId={msg.id}
                              replyAll={composer!.replyAll}
                              onCancel={() => setComposer(null)}
                              onSent={() => handleReplySent(msg.id)}
                            />
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
