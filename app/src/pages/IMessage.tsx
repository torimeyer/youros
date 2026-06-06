import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react'
import { formatSmartDate } from '../lib/time'
import Icon from '../components/Icon'
import PageShell from '../components/PageShell'
import { ConnectCard, LoadingState, EmptyState, ErrorBanner } from '../components/ui'
import { api } from '../lib/api'

interface Conversation {
  id: number
  identifier: string
  display_name: string
  has_contact_name: boolean
  service: string
  last_message_date: string
  last_message_preview: string
  message_count: number
  unread_count: number
}

interface Attachment {
  filename: string
  mime_type: string
  transfer_name: string
}

interface Message {
  id: number
  text: string
  date: string
  is_from_me: boolean
  is_read: boolean
  sender: string
  attachments?: Attachment[]
}

interface SearchResult {
  message_id: number
  text: string
  date: string
  is_from_me: boolean
  chat_id: number
  chat_identifier: string
  chat_display_name: string
  sender: string
}

interface ContactSuggestion {
  name: string
  phone: string | null
  email: string | null
  identifier: string
}

interface StatusResponse {
  available: boolean
  reason: string | null
}

interface Contact {
  name: string
  phone_numbers: string[]
  emails: string[]
}

const AttachmentImage = memo(function AttachmentImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div
        data-testid="attachment-placeholder"
        className="bg-slate-100/60 dark:bg-slate-700/60 border border-slate-600 rounded-lg px-3 py-2 text-xs text-slate-600 dark:text-slate-400 flex items-center gap-2 mb-1"
      >
        <Icon name="image" size={14} />
        <span className="truncate">{alt}</span>
      </div>
    )
  }
  return (
    <img
      data-testid="attachment-image"
      src={src}
      alt={alt}
      className="max-w-xs max-h-64 rounded-lg mb-1 block"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
})

// Seed from localStorage so the page paints immediately
const IMESSAGE_CACHE_KEY = 'myos.imessageCache.v1'
const IMESSAGE_CONNECTION_KEY = 'myos.imessageConnection.v1'

type ConnectionState = 'loading' | 'connected' | 'not_connected'

function readCache(): Conversation[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(IMESSAGE_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as Conversation[]) : []
  } catch {
    return []
  }
}

function writeCache(conversations: Conversation[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(IMESSAGE_CACHE_KEY, JSON.stringify(conversations))
  } catch {
    // Quota or serialization errors are not fatal.
  }
}

function writeConnectionCache(state: 'connected' | 'not_connected') {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(IMESSAGE_CONNECTION_KEY, state)
  } catch {
    // Quota or serialization errors are not fatal.
  }
}



function normalizePhoneStr(s: string): string {
  return s.replace(/\D/g, '')
}

function ContactPicker({
  value,
  onChange,
  onSelectConversation,
  contacts: externalContacts = [],
  conversations: externalConversations = [],
  className,
}: {
  value: string
  onChange: (identifier: string) => void
  onSelectConversation?: (chatId: number) => void
  contacts?: Contact[]
  conversations?: Conversation[]
  className?: string
}) {
  const [inputValue, setInputValue] = useState(value)
  const [apiSuggestions, setApiSuggestions] = useState<ContactSuggestion[]>([])
  const [open, setOpen] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!value) setInputValue('')
  }, [value])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const localResults = useMemo(() => {
    if (!inputValue.trim()) return { contacts: [] as Contact[], conversations: [] as Conversation[] }
    const q = inputValue.toLowerCase()
    const qDigits = normalizePhoneStr(inputValue)

    const matchedContacts = externalContacts.filter((c) =>
      c.name.toLowerCase().includes(q) ||
      c.phone_numbers.some((p) => p.toLowerCase().includes(q) || (qDigits.length >= 3 && normalizePhoneStr(p).includes(qDigits))) ||
      c.emails.some((e) => e.toLowerCase().includes(q))
    )

    const matchedConversations = externalConversations.filter((c) =>
      (c.display_name || '').toLowerCase().includes(q) ||
      c.identifier.toLowerCase().includes(q) ||
      (qDigits.length >= 3 && normalizePhoneStr(c.identifier).includes(qDigits))
    )

    return { contacts: matchedContacts, conversations: matchedConversations }
  }, [inputValue, externalContacts, externalConversations])

  const hasResults =
    localResults.contacts.length > 0 ||
    localResults.conversations.length > 0 ||
    apiSuggestions.length > 0

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value
    setInputValue(v)
    onChange(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!v.trim()) {
      setApiSuggestions([])
      setOpen(false)
      return
    }
    setOpen(true)
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.get<{ contacts: ContactSuggestion[] }>(
          `/imessage/contacts/search?q=${encodeURIComponent(v)}`
        )
        setApiSuggestions(res.contacts || [])
      } catch {
        setApiSuggestions([])
      }
    }, 200)
  }

  const handleSelectApiSuggestion = (c: ContactSuggestion) => {
    setInputValue(`${c.name} (${c.identifier})`)
    onChange(c.identifier)
    setApiSuggestions([])
    setOpen(false)
  }

  const handleSelectLocalContact = (c: Contact) => {
    const nums = c.phone_numbers.map(normalizePhoneStr).filter(Boolean)
    const emails = c.emails.map((e) => e.toLowerCase())
    const match = externalConversations.find((conv) => {
      const id = conv.identifier.toLowerCase()
      const idDigits = normalizePhoneStr(conv.identifier)
      return emails.includes(id) || nums.some((n) => idDigits === n || idDigits.includes(n))
    })
    if (match && onSelectConversation) {
      onSelectConversation(match.id)
      setInputValue('')
      onChange('')
    } else {
      const identifier = c.phone_numbers[0] || c.emails[0] || ''
      setInputValue(identifier)
      onChange(identifier)
    }
    setApiSuggestions([])
    setOpen(false)
  }

  const handleSelectConversation = (conv: Conversation) => {
    if (onSelectConversation) onSelectConversation(conv.id)
    setInputValue('')
    onChange('')
    setApiSuggestions([])
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        placeholder="Name, phone number, or email"
        value={inputValue}
        onChange={handleChange}
        onFocus={() => hasResults && setOpen(true)}
        className={className}
        data-testid="contact-picker-input"
      />
      {open && hasResults && (
        <ul
          role="listbox"
          className="absolute z-10 left-0 right-0 top-full mt-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg overflow-hidden"
        >
          {localResults.contacts.map((c, i) => (
            <li key={`lc-${i}`}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                data-testid="contact-picker-contact-row"
                onClick={() => handleSelectLocalContact(c)}
                className="w-full text-left px-3 py-2 hover:bg-slate-200 dark:hover:bg-slate-700 text-sm transition-colors flex items-center gap-2"
              >
                <div className="w-6 h-6 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center shrink-0">
                  <span className="text-xs font-medium text-slate-700 dark:text-slate-300">
                    {(c.name || '?').charAt(0).toUpperCase()}
                  </span>
                </div>
                <span className="font-medium text-slate-900 dark:text-white">{c.name}</span>
                <span className="text-slate-600 dark:text-slate-400 text-xs truncate">
                  {c.phone_numbers[0] || c.emails[0] || ''}
                </span>
              </button>
            </li>
          ))}
          {localResults.conversations.map((c) => (
            <li key={`lv-${c.id}`}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                data-testid="contact-picker-convo-row"
                onClick={() => handleSelectConversation(c)}
                className="w-full text-left px-3 py-2 hover:bg-slate-200 dark:hover:bg-slate-700 text-sm transition-colors flex items-center gap-2"
              >
                <div className="w-6 h-6 rounded-full bg-blue-900/40 flex items-center justify-center shrink-0">
                  <Icon name="chat_bubble" size={12} className="text-blue-600 dark:text-blue-400" />
                </div>
                <span className="font-medium text-slate-900 dark:text-white truncate">
                  {c.display_name || c.identifier}
                </span>
                <span className="text-slate-600 dark:text-slate-400 text-xs truncate">{c.last_message_preview}</span>
              </button>
            </li>
          ))}
          {apiSuggestions.map((s, i) => (
            <li key={`api-${i}`}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => handleSelectApiSuggestion(s)}
                className="w-full text-left px-3 py-2 hover:bg-slate-200 dark:hover:bg-slate-700 text-sm transition-colors flex items-center gap-2"
              >
                <span className="font-medium text-slate-900 dark:text-white">{s.name}</span>
                <span className="text-slate-600 dark:text-slate-400 text-xs truncate">{s.identifier}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function IMessage() {
  const [connectionState, setConnectionState] = useState<ConnectionState>('loading')
  const [statusReason, setStatusReason] = useState<string | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>(() => readCache())
  const [loading, setLoading] = useState<boolean>(() => readCache().length === 0)
  const [selectedChat, setSelectedChat] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const messagesScrollRef = useRef<HTMLDivElement | null>(null)
  const locallyReadRef = useRef<Set<number>>(new Set())

  useEffect(() => {
    if (messagesLoading) return
    const el = messagesScrollRef.current
    if (!el) return
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight
    })
  }, [selectedChat, messages, messagesLoading])
  const [searchResults] = useState<SearchResult[] | null>(null)
  const [sendRecipient, setSendRecipient] = useState('')
  const [sendText, setSendText] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [sendSuccess, setSendSuccess] = useState(false)
  const [replyText, setReplyText] = useState('')
  const [replySending, setReplySending] = useState(false)
  const [replyError, setReplyError] = useState<string | null>(null)
  const [replySuccess, setReplySuccess] = useState(false)
  const [saveContactChatId, setSaveContactChatId] = useState<number | null>(null)
  const [saveContactName, setSaveContactName] = useState('')
  const [saveContactSaving, setSaveContactSaving] = useState(false)
  const [saveContactError, setSaveContactError] = useState<string | null>(null)

  const [contacts, setContacts] = useState<Contact[]>([])

  const fetchConversations = useCallback(async () => {
    try {
      const res = await api.get<{ conversations: Conversation[] }>('/imessage/conversations')
      const fetched = (res.conversations || []).map(c =>
        locallyReadRef.current.has(c.id) ? { ...c, unread_count: 0 } : c
      )
      setConversations(fetched)
      writeCache(fetched)
    } catch {
      setConversations((prev) => (prev.length > 0 ? prev : []))
    }
  }, [])

  const fetchMessages = useCallback(async (chatId: number) => {
    setMessagesLoading(true)
    try {
      const res = await api.get<{ messages: Message[] }>(`/imessage/conversations/${chatId}/messages`)
      setMessages(res.messages || [])
    } catch {
      setMessages([])
    } finally {
      setMessagesLoading(false)
    }
  }, [])

  useEffect(() => {
    const hasCached = readCache().length > 0
    if (!hasCached) setLoading(true)
    ;(async () => {
      try {
        const s = await api.get<StatusResponse>('/imessage/status')
        if (s.available) {
          setConnectionState('connected')
          writeConnectionCache('connected')
          await fetchConversations()
        } else {
          setStatusReason(s.reason ?? null)
          setConnectionState('not_connected')
          writeConnectionCache('not_connected')
        }
      } catch {
        setStatusReason('Could not connect to the yourOS backend. Make sure the backend is running, then refresh this page.')
        setConnectionState('not_connected')
        writeConnectionCache('not_connected')
      }
      setLoading(false)
    })()
    window.addEventListener('focus', fetchConversations)
    return () => window.removeEventListener('focus', fetchConversations)
  }, [fetchConversations])

  const handleSelectChat = (chatId: number) => {
    if (selectedChat === chatId) {
      setSelectedChat(null)
      setMessages([])
      return
    }
    setSelectedChat(chatId)
    setReplyText('')
    setReplyError(null)
    setReplySuccess(false)
    fetchMessages(chatId)
    locallyReadRef.current.add(chatId)
    setConversations(prev => {
      const updated = prev.map(c =>
        c.id === chatId ? { ...c, unread_count: 0 } : c
      )
      writeCache(updated)
      return updated
    })
  }

  const handleReply = async (chatId: number, text: string) => {
    if (!text) return
    setReplySending(true)
    setReplyError(null)
    setReplySuccess(false)
    try {
      await api.post(`/imessage/conversations/${chatId}/reply`, { text })
      setReplySuccess(true)
      setReplyText('')
      await fetchConversations()
      await fetchMessages(chatId)
      setTimeout(() => setReplySuccess(false), 3000)
    } catch (err: unknown) {
      setReplyError((err as Error)?.message || 'Failed to send the reply.')
    } finally {
      setReplySending(false)
    }
  }

  const handleSend = async (recipient: string, text: string) => {
    if (!recipient || !text) return
    setSending(true)
    setSendError(null)
    setSendSuccess(false)
    try {
      await api.post('/imessage/send', { recipient, text })
      setSendSuccess(true)
      setSendText('')
      setReplyText('')
      await fetchConversations()
      if (selectedChat !== null) {
        await fetchMessages(selectedChat)
      }
      setTimeout(() => setSendSuccess(false), 3000)
    } catch (err: unknown) {
      setSendError((err as Error)?.message || 'Failed to send the message.')
    } finally {
      setSending(false)
    }
  }

  const handleSaveContact = async (identifier: string, name: string) => {
    if (!name.trim()) return
    setSaveContactSaving(true)
    setSaveContactError(null)
    try {
      await api.post('/imessage/contacts/save', { identifier, name: name.trim() })
      setSaveContactChatId(null)
      setSaveContactName('')
      await fetchConversations()
    } catch (err: unknown) {
      setSaveContactError((err as Error)?.message || 'Could not save the contact.')
    } finally {
      setSaveContactSaving(false)
    }
  }

  useEffect(() => {
    api.get<{ contacts: Contact[] }>('/contacts')
      .then((res) => setContacts(res.contacts || []))
      .catch(() => {})
  }, [])

  const cardClass = 'bg-white dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 p-3 sm:p-4 rounded-xl'

  if (connectionState === 'loading' && conversations.length === 0) {
    return (
      <PageShell title="Messages">
          <LoadingState variant="spinner" />
      </PageShell>
    )
  }

  if (connectionState === 'not_connected') {
    return (
      <PageShell title="Messages">
          <ConnectCard
            icon="chat_bubble"
            accentColor="#22c55e"
            title="iMessage not available"
            description={statusReason || 'iMessage integration requires macOS with Full Disk Access enabled.'}
            primaryAction={
              statusReason?.includes('Full Disk Access') ? (
                <div className="bg-slate-100 dark:bg-slate-800/50 p-4 rounded-xl text-sm text-slate-700 dark:text-slate-300 text-left w-full">
                  <p className="font-medium mb-2">How to enable:</p>
                  <ol className="list-decimal list-inside space-y-1 text-slate-600 dark:text-slate-400">
                    <li>Open System Settings</li>
                    <li>Go to Privacy &amp; Security</li>
                    <li>Click Full Disk Access</li>
                    <li>Enable access for your terminal app (Terminal, iTerm2, etc.)</li>
                    <li>Restart the terminal and try again</li>
                  </ol>
                </div>
              ) : null
            }
          />
      </PageShell>
    )
  }

  return (
    <PageShell title="Messages">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-slate-900 dark:text-white">Messages</h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-0.5">
              {conversations.length} conversation{conversations.length !== 1 ? 's' : ''}
              {contacts.length > 0 && ` · ${contacts.length} contact${contacts.length !== 1 ? 's' : ''}`}
            </p>
          </div>
        </div>

        {/* Search results (message search, not people search) */}
        {searchResults !== null && (
          <div className={`${cardClass} mb-4`}>
            <div className="flex items-center gap-2 mb-3">
              <Icon name="search" className="text-blue-600 dark:text-blue-400" size={18} />
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                Search results ({searchResults.length})
              </h2>
            </div>
            {searchResults.length === 0 ? (
              <p className="text-sm text-slate-500 py-4 text-center">No messages found</p>
            ) : (
              <div className="divide-y divide-slate-200/60 dark:divide-slate-800/60">
                {searchResults.map((r) => (
                  <button
                    key={r.message_id}
                    onClick={() => handleSelectChat(r.chat_id)}
                    className="w-full text-left px-3 py-3 rounded-lg hover:bg-slate-50/40 dark:hover:bg-slate-800/40 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">
                        {r.chat_display_name}
                      </span>
                      <span className="text-xs text-slate-500 shrink-0">
                        {formatSmartDate(r.date)}
                      </span>
                    </div>
                    <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 truncate">{r.text}</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* New message composer */}
        <div className={`${cardClass} mb-4`}>
          <div className="flex items-center gap-2 mb-3">
            <Icon name="edit" className="text-blue-600 dark:text-blue-400" size={18} />
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">Send a message</h2>
          </div>
          <div className="space-y-2">
            <ContactPicker
              value={sendRecipient}
              onChange={setSendRecipient}
              onSelectConversation={handleSelectChat}
              contacts={contacts}
              conversations={conversations}
              className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Type your message..."
                value={sendText}
                onChange={(e) => setSendText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleSend(sendRecipient, sendText)
                  }
                }}
                className="flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={() => handleSend(sendRecipient, sendText)}
                disabled={sending || !sendRecipient || !sendText}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {sending ? (
                  <Icon name="progress_activity" size={16} className="animate-spin" />
                ) : (
                  'Send'
                )}
              </button>
            </div>
            {sendError && (
              <ErrorBanner message={sendError} />
            )}
            {sendSuccess && (
              <div className="flex items-center gap-2 text-sm text-emerald-400">
                <Icon name="check_circle" size={16} />
                Message sent
              </div>
            )}
          </div>
        </div>

        {/* Conversations list */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-4">
            <Icon name="chat_bubble" className="text-blue-600 dark:text-blue-400" size={18} />
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">Conversations</h2>
          </div>

          {loading ? (
            <LoadingState variant="spinner" message="Loading conversations..." />
          ) : conversations.length === 0 ? (
            <EmptyState icon="chat_bubble_outline" title="No conversations here yet." />
          ) : (
            <div className="divide-y divide-slate-200/60 dark:divide-slate-800/60">
              {conversations.map((convo) => {
                const isSelected = selectedChat === convo.id
                return (
                  <div key={convo.id} className="py-1">
                    <button
                      onClick={() => handleSelectChat(convo.id)}
                      aria-expanded={isSelected}
                      className="w-full text-left px-3 py-3 rounded-lg transition-colors hover:bg-slate-50/40 dark:hover:bg-slate-800/40"
                    >
                      <div className="flex items-start gap-3">
                        <div className="mt-1.5 shrink-0">
                          {convo.unread_count > 0 ? (
                            <span className="w-2 h-2 rounded-full bg-blue-400 block" />
                          ) : (
                            <span className="w-2 h-2 rounded-full bg-transparent block" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between gap-2">
                            <p className={`text-sm truncate ${convo.unread_count > 0 ? 'font-semibold text-slate-900 dark:text-white' : 'text-slate-700 dark:text-slate-300'}`}>
                              {convo.display_name || convo.identifier}
                            </p>
                            <span className="text-xs text-slate-500 shrink-0">
                              {formatSmartDate(convo.last_message_date)}
                            </span>
                          </div>
                          <p className="text-xs text-slate-500 truncate mt-0.5">
                            {convo.last_message_preview}
                          </p>
                        </div>
                        <div className="shrink-0 mt-1 flex items-center gap-2">
                          {convo.unread_count > 0 && (
                            <span className="bg-blue-500/20 text-blue-600 dark:text-blue-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                              {convo.unread_count}
                            </span>
                          )}
                          <Icon
                            name={isSelected ? 'expand_less' : 'expand_more'}
                            size={18}
                            className="text-slate-500"
                          />
                        </div>
                      </div>
                    </button>

                    {isSelected && (
                      <div className="px-3 pb-3">
                        {/* Save to Contacts — shown only when no contact name is known */}
                        {!convo.has_contact_name && (
                          <div className="mb-3 p-2.5 bg-slate-50/60 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-xl">
                            {saveContactChatId === convo.id ? (
                              <div className="flex flex-col gap-2">
                                <p className="text-xs text-slate-600 dark:text-slate-400">What name should we use for {convo.identifier}?</p>
                                <div className="flex gap-2">
                                  <input
                                    data-testid="save-contact-name-input"
                                    type="text"
                                    placeholder="Enter a name"
                                    value={saveContactName}
                                    onChange={(e) => setSaveContactName(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') handleSaveContact(convo.identifier, saveContactName)
                                      if (e.key === 'Escape') { setSaveContactChatId(null); setSaveContactName('') }
                                    }}
                                    autoFocus
                                    className="flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                                  />
                                  <button
                                    data-testid="save-contact-submit"
                                    onClick={() => handleSaveContact(convo.identifier, saveContactName)}
                                    disabled={saveContactSaving || !saveContactName.trim()}
                                    className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                                  >
                                    {saveContactSaving ? (
                                      <Icon name="progress_activity" size={14} className="animate-spin" />
                                    ) : 'Save'}
                                  </button>
                                  <button
                                    onClick={() => { setSaveContactChatId(null); setSaveContactName('') }}
                                    className="px-2 py-1.5 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 rounded-lg text-sm transition-colors"
                                  >
                                    <Icon name="close" size={14} />
                                  </button>
                                </div>
                                {saveContactError && (
                                  <p className="text-xs text-red-600 dark:text-red-400">{saveContactError}</p>
                                )}
                              </div>
                            ) : (
                              <button
                                data-testid="save-to-contacts-btn"
                                onClick={() => { setSaveContactChatId(convo.id); setSaveContactName(''); setSaveContactError(null) }}
                                className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
                              >
                                <Icon name="person_add" size={14} />
                                Save to Contacts
                              </button>
                            )}
                          </div>
                        )}

                        <div
                          ref={messagesScrollRef}
                          className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-4 max-h-96 overflow-y-auto"
                        >
                          {messagesLoading ? (
                            <LoadingState variant="spinner" message="Loading messages..." />
                          ) : messages.length === 0 ? (
                            <p className="text-sm text-slate-500 text-center py-4">No messages yet</p>
                          ) : (
                            <div className="space-y-2">
                              {messages.map((msg) => (
                                <div
                                  key={msg.id}
                                  className={`flex ${msg.is_from_me ? 'justify-end' : 'justify-start'}`}
                                >
                                  <div
                                    className={`max-w-[80%] px-3 py-2 rounded-2xl text-sm ${
                                      msg.is_from_me
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200'
                                    }`}
                                  >
                                    {msg.attachments?.filter(a => a.mime_type?.startsWith('image/')).map((att, i) => (
                                      <AttachmentImage
                                        key={i}
                                        src={`/api/imessage/attachment?path=${encodeURIComponent(att.filename)}`}
                                        alt={att.transfer_name || att.filename.split('/').pop() || 'image'}
                                      />
                                    ))}
                                    {msg.text?.replace(/￼/g, '').trim() ? (
                                      <p className="whitespace-pre-wrap">{msg.text.replace(/￼/g, '').trim()}</p>
                                    ) : null}
                                    <p className={`text-[10px] mt-1 ${msg.is_from_me ? 'text-blue-200' : 'text-slate-500'}`}>
                                      {formatSmartDate(msg.date)}
                                    </p>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                        </div>

                        {/* Reply form kept outside the scroll area so it stays visible */}
                        <div className="mt-2 px-1">
                          <div className="flex gap-2">
                            <input
                              type="text"
                              placeholder="Reply..."
                              value={replyText}
                              onChange={(e) => setReplyText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && !e.shiftKey) {
                                  e.preventDefault()
                                  handleReply(convo.id, replyText)
                                }
                              }}
                              className="flex-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                            />
                            <button
                              aria-label="Send reply"
                              onClick={() => handleReply(convo.id, replyText)}
                              disabled={replySending || !replyText}
                              className="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors disabled:opacity-50"
                            >
                              {replySending ? (
                                <Icon name="progress_activity" size={16} className="animate-spin" />
                              ) : (
                                <Icon name="send" size={16} />
                              )}
                            </button>
                          </div>
                          {replyError && (
                            <div className="mt-1">
                              <ErrorBanner message={replyError} />
                            </div>
                          )}
                          {replySuccess && (
                            <div className="flex items-center gap-1 text-xs text-emerald-400 mt-1">
                              <Icon name="check_circle" size={13} />
                              Sent
                            </div>
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
      </PageShell>
  )
}
