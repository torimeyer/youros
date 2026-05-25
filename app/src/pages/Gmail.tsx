import { useState, useEffect, useCallback } from 'react'
import { formatSmartDate, formatTime } from '../lib/time'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import GmailReplyComposer from '../components/GmailReplyComposer'
import ConfirmModal from '../components/ConfirmModal'
import GoogleSetupGuideModal from '../components/GoogleSetupGuideModal'
import { useConfirm } from '../hooks/useConfirm'
import { ConnectCard, LoadingState, EmptyState } from '../components/ui'
import { api } from '../lib/api'
import { useAppStore } from '../stores/app'

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

// Seed the message list from localStorage so the page paints rows
// immediately instead of showing a spinner while the Gmail API
// responds (which can take 25+ seconds on a cold cache). Same
// pattern as Tasks.tsx. Needle 316.
const GMAIL_CACHE_KEY = 'myos.gmailCache.v1'

function readGmailCache(): GmailMessage[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(GMAIL_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as GmailMessage[]) : []
  } catch {
    return []
  }
}

function writeGmailCache(messages: GmailMessage[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(GMAIL_CACHE_KEY, JSON.stringify(messages))
  } catch {
    // Quota or serialization errors are not fatal.
  }
}



function gmailUrl(messageId: string): string {
  return `https://mail.google.com/mail/u/0/#inbox/${messageId}`
}

export default function Gmail() {
  const [searchParams] = useSearchParams()
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [messages, setMessages] = useState<GmailMessage[]>(() => readGmailCache())
  const [loading, setLoading] = useState<boolean>(() => readGmailCache().length === 0)
  const [syncing, setSyncing] = useState(false)
  const [lastSynced, setLastSynced] = useState<Date | null>(null)
  const [markingRead, setMarkingRead] = useState<Set<string>>(new Set())
  const [apiNotEnabled, setApiNotEnabled] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [composer, setComposer] = useState<ComposerState>(null)
  const [sendCapability, setSendCapability] = useState<SendCapability | null>(null)
  const [sentConfirmationId, setSentConfirmationId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<Record<string, 'loading' | 'done' | 'error'>>({})
  const [taskResult, setTaskResult] = useState<Record<string, { title: string }>>({})
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [trashing, setTrashing] = useState<Set<string>>(new Set())
  const [bulkTrashing, setBulkTrashing] = useState(false)
  const [trashError, setTrashError] = useState<string | null>(null)

  const { gmailUnreadAtTop, setGmailUnreadAtTop } = useAppStore()

  const displayedMessages = [...messages].sort((a, b) => {
    if (gmailUnreadAtTop) {
      if (a.is_unread && !b.is_unread) return -1
      if (!a.is_unread && b.is_unread) return 1
    }
    // Default sort is by date descending (newest first). The API returns
    // them this way, but we re-sort here to ensure the unread-at-top
    // overlay stays stable when toggled.
    return new Date(b.date).getTime() - new Date(a.date).getTime()
  })

  // In-app confirm dialog (replaces window.confirm for bulk trash).
  const { confirm, confirmProps } = useConfirm()

  // Show the shared Google setup guide modal when the user clicks the
  // secondary "Need setup help?" link on the connect panel. Lives at the
  // page level so it can be rendered alongside ConnectCard.
  const [showSetupGuide, setShowSetupGuide] = useState(false)
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false)

  const handleCreateTask = async (e: React.MouseEvent, messageId: string) => {
    e.stopPropagation()
    setTaskStatus((prev) => ({ ...prev, [messageId]: 'loading' }))
    try {
      const res = await api.post<{ ok: boolean; title: string; task_id: string }>(
        '/gmail/to-task',
        { message_id: messageId },
      )
      setTaskStatus((prev) => ({ ...prev, [messageId]: 'done' }))
      setTaskResult((prev) => ({ ...prev, [messageId]: { title: res.title } }))
    } catch {
      setTaskStatus((prev) => ({ ...prev, [messageId]: 'error' }))
    }
  }

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
      const fetched = res.messages || []
      setMessages(fetched)
      writeGmailCache(fetched)
      setLastSynced(new Date())
      setApiNotEnabled(false)
    } catch (err: unknown) {
      // Keep existing messages on failure so a timeout does not blank
      // the UI. Only clear if we have nothing cached.
      setMessages((prev) => prev.length > 0 ? prev : [])
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      if (typeof detail === 'object' && detail !== null) {
        const d = detail as Record<string, unknown>
        if (d.api_not_enabled) setApiNotEnabled(true)
        if (d.needs_reauth) {
          // Token was revoked; mirror what auth/status will return on next
          // poll so the ConnectCard appears immediately without waiting.
          setAuthStatus((prev) =>
            prev ? { ...prev, needs_reauth: true } : { authenticated: true, needs_reauth: true, email: null, unread_count: 0 }
          )
        }
      }
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
    api.get<{ google_oauth_available?: boolean }>('/secrets/key-status')
      .then((data) => setGoogleOAuthAvailable(data.google_oauth_available ?? false))
      .catch(() => {})
  }, [])

  useEffect(() => {
    // Only show the loading spinner if we have no cached messages.
    // When cached data exists, the page paints rows immediately and
    // the background fetch updates them when it arrives. Needle 316.
    const hasCached = readGmailCache().length > 0
    if (!hasCached) setLoading(true)
    ;(async () => {
      try {
        const status = await api.get<AuthStatus>('/gmail/auth/status')
        setAuthStatus(status)
        if (status.authenticated && !status.needs_reauth) {
          // Fire both in parallel. Don't block on fetchMessages when
          // we already have cached rows showing.
          const msgPromise = fetchMessages()
          await fetchSendCapability()
          if (!hasCached) await msgPromise
          // If we had cache, msgPromise resolves in the background.
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
      const updated = messages.map((m) => (m.id === msg.id ? { ...m, is_unread: false } : m))
      setMessages(updated)
      writeGmailCache(updated)
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

  const handleToggleSelect = (e: React.MouseEvent | React.ChangeEvent, messageId: string) => {
    e.stopPropagation()
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(messageId)) {
        next.delete(messageId)
      } else {
        next.add(messageId)
      }
      return next
    })
  }

  const removeFromList = (ids: string[]) => {
    setMessages((prev) => {
      const next = prev.filter((m) => !ids.includes(m.id))
      writeGmailCache(next)
      return next
    })
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const id of ids) next.delete(id)
      return next
    })
    if (ids.length && expandedId && ids.includes(expandedId)) {
      setExpandedId(null)
      setComposer(null)
    }
  }

  const handleTrashMessage = async (e: React.MouseEvent, messageId: string) => {
    e.stopPropagation()
    setTrashError(null)
    setTrashing((prev) => new Set(prev).add(messageId))
    try {
      await api.delete(`/gmail/messages/${messageId}`)
      removeFromList([messageId])
    } catch {
      setTrashError('Could not move the message to Trash. Try again in a moment.')
    } finally {
      setTrashing((prev) => {
        const next = new Set(prev)
        next.delete(messageId)
        return next
      })
    }
  }

  const handleBulkTrash = async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    const confirmed = await confirm({
      title: `Move ${ids.length} ${ids.length === 1 ? 'message' : 'messages'} to Trash?`,
      message: "You can still recover them from Gmail's Trash for 30 days.",
      confirmLabel: 'Move to Trash',
      cancelLabel: 'Cancel',
      danger: true,
    })
    if (!confirmed) return
    setTrashError(null)
    setBulkTrashing(true)
    try {
      const res = await api.post<{ succeeded: string[]; failed: { id: string; error: string }[]; count: number }>(
        '/gmail/messages/batch-delete',
        { ids, permanent: false }
      )
      const succeeded = res.succeeded || []
      removeFromList(succeeded)
      if (res.failed && res.failed.length > 0) {
        setTrashError(
          `Moved ${succeeded.length} to Trash. ${res.failed.length} failed.`
        )
      }
    } catch {
      setTrashError('Could not move the selected messages to Trash. Try again.')
    } finally {
      setBulkTrashing(false)
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

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-3 sm:p-4 rounded-xl'

  if (loading || authStatus === null) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (!authStatus?.authenticated || authStatus.needs_reauth) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="mail"
            accentColor="#ef4444"
            title={authStatus?.needs_reauth ? 'Gmail access needs to be updated' : 'Connect Gmail'}
            description={
              authStatus?.needs_reauth
                ? 'Reconnect your Google account to give myOS permission to read your Gmail. This uses the same account you already connected for Drive.'
                : 'See your unread emails without leaving myOS. This uses the same Google account as Drive and Calendar, so no extra credentials are needed.'
            }
            primaryAction={
              <div className="w-full space-y-3">
                {googleOAuthAvailable ? (
                  <button
                    onClick={handleConnect}
                    className="w-full py-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
                    data-testid="connect-google-button-gmail"
                  >
                    {authStatus?.needs_reauth ? 'Reconnect' : 'Connect Google account'}
                  </button>
                ) : (
                  <button
                    onClick={handleConnect}
                    className="w-full py-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
                  >
                    {authStatus?.needs_reauth ? 'Reconnect' : 'Connect Google account'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowSetupGuide(true)}
                  className="w-full text-xs text-slate-500 hover:text-slate-300 underline"
                >
                  Need setup help? See the guide
                </button>
              </div>
            }
            error={connectError ?? undefined}
          />
        </div>
        {showSetupGuide && <GoogleSetupGuideModal onClose={() => setShowSetupGuide(false)} />}
      </div>
    )
  }

  if (apiNotEnabled) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Gmail" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="warning"
            accentColor="#f59e0b"
            title="Gmail API not enabled"
            description="Your Google Cloud project has the Gmail API disabled. You need to turn it on once. Reconnecting will not fix this."
            primaryAction={
              <div className="w-full space-y-3">
                <a
                  href="https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                  target="_blank"
                  rel="noreferrer"
                  className="w-full block text-center py-3 bg-red-600 hover:bg-red-700 rounded-xl font-medium transition-colors"
                >
                  Enable Gmail API in Google Cloud
                </a>
                <p className="text-xs text-slate-500 text-center">
                  After clicking Enable on Google's page, wait 1-2 minutes, then click Retry.
                </p>
                <button
                  onClick={() => { setApiNotEnabled(false); fetchMessages() }}
                  className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors"
                >
                  Retry
                </button>
              </div>
            }
          />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Gmail" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header row */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl sm:text-2xl font-bold">Gmail</h1>
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
                Synced {formatTime(lastSynced)}
              </span>
            )}
            <button
              onClick={() => setGmailUnreadAtTop(!gmailUnreadAtTop)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                gmailUnreadAtTop ? 'bg-red-500/20 text-red-400' : 'bg-slate-800 text-slate-400'
              }`}
            >
              <Icon name="vertical_align_top" size={16} />
              Unread first
            </button>
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
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <Icon name="inbox" className="text-red-400" size={18} />
            <h2 className="text-base font-semibold">Inbox</h2>
            {selectedIds.size > 0 && (
              <>
                <span className="ml-2 text-sm text-slate-400">
                  {selectedIds.size} selected
                </span>
                <button
                  type="button"
                  onClick={handleBulkTrash}
                  disabled={bulkTrashing}
                  aria-label="Trash selected messages"
                  className="ml-auto flex items-center gap-1.5 px-3 py-1.5 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                >
                  <Icon name="delete" size={16} />
                  {bulkTrashing ? 'Moving to Trash...' : 'Trash selected'}
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="flex items-center gap-1 px-2 py-1 text-xs text-slate-400 hover:text-slate-200"
                >
                  Clear
                </button>
              </>
            )}
          </div>
          {trashError && (
            <div className="mb-3 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-300">
              {trashError}
            </div>
          )}

          {syncing && displayedMessages.length === 0 ? (
            <LoadingState variant="spinner" message="Loading your inbox..." />
          ) : displayedMessages.length === 0 ? (
            <EmptyState
              icon="mark_email_read"
              title="No messages in your inbox"
              description="Pull the latest from Gmail to see what's waiting."
              action={{ label: 'Sync now', onClick: handleSync }}
            />
          ) : (
            <div className="divide-y divide-slate-800/60">
              {displayedMessages.map((msg) => {
                const isExpanded = expandedId === msg.id
                const isComposing = composer?.messageId === msg.id
                const canSend = sendCapability?.has_send_scope === true
                const isSelected = selectedIds.has(msg.id)
                return (
                  <div key={msg.id} className="py-1">
                   <div className="flex items-start gap-2">
                    <label
                      className="pt-5 pl-1 shrink-0 cursor-pointer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select message from ${msg.from_name || msg.from_email}`}
                        checked={isSelected}
                        onChange={(e) => handleToggleSelect(e, msg.id)}
                        className="w-4 h-4 accent-red-500 cursor-pointer"
                      />
                    </label>
                    <button
                      onClick={() => handleMessageClick(msg)}
                      disabled={markingRead.has(msg.id)}
                      aria-expanded={isExpanded}
                      className={`flex-1 min-w-0 text-left px-3 py-3 rounded-lg transition-colors hover:bg-slate-800/40 disabled:opacity-50 ${
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
                            <span className="text-xs text-slate-500 shrink-0">{formatSmartDate(msg.date)}</span>
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
                   </div>

                    {isExpanded && (
                      <div className="px-3 pb-3">
                        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                          <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
                            <div className="text-xs text-slate-500 min-w-0 truncate">
                              From {msg.from_name || msg.from_email}
                              {msg.from_name ? ` (${msg.from_email})` : ''}
                            </div>
                            <div className="flex items-center gap-2 flex-wrap">
                              <button
                                type="button"
                                onClick={(e) => handleCreateTask(e, msg.id)}
                                disabled={taskStatus[msg.id] === 'loading' || taskStatus[msg.id] === 'done'}
                                className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-50"
                              >
                                {taskStatus[msg.id] === 'loading' ? (
                                  <><Icon name="progress_activity" size={14} className="animate-spin" /> Creating task...</>
                                ) : taskStatus[msg.id] === 'done' ? (
                                  <><Icon name="check_circle" size={14} className="text-green-400" /> Task created</>
                                ) : taskStatus[msg.id] === 'error' ? (
                                  <><Icon name="error" size={14} className="text-red-400" /> Failed</>
                                ) : (
                                  <><Icon name="add_task" size={14} /> Create task</>
                                )}
                              </button>
                              <button
                                type="button"
                                onClick={(e) => handleTrashMessage(e, msg.id)}
                                disabled={trashing.has(msg.id)}
                                aria-label="Move to Trash"
                                className="flex items-center gap-1 text-xs text-slate-400 hover:text-red-400 disabled:opacity-50"
                              >
                                {trashing.has(msg.id) ? (
                                  <><Icon name="progress_activity" size={14} className="animate-spin" /> Moving...</>
                                ) : (
                                  <><Icon name="delete" size={14} /> Move to Trash</>
                                )}
                              </button>
                              <button
                                type="button"
                                onClick={(e) => handleOpenInGmail(e, msg.id)}
                                className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
                              >
                                <Icon name="open_in_new" size={14} />
                                Open in Gmail
                              </button>
                            </div>
                          </div>
                          <p className="text-sm text-slate-200 whitespace-pre-wrap break-words">
                            {msg.snippet}
                          </p>

                          {sentConfirmationId === msg.id && (
                            <div className="mt-3 flex items-center gap-2 text-sm text-emerald-400">
                              <Icon name="check_circle" size={16} />
                              Reply sent
                            </div>
                          )}

                          {taskStatus[msg.id] === 'done' && taskResult[msg.id] && (
                            <div className="mt-3 flex items-center gap-2 text-sm text-emerald-400">
                              <Icon name="task_alt" size={16} />
                              Task created: {taskResult[msg.id].title}
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
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
