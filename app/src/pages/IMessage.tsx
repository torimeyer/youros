import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
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

function readConnectionCache(): ConnectionState {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return 'loading'
    const raw = window.localStorage.getItem(IMESSAGE_CONNECTION_KEY)
    if (raw === 'connected' || raw === 'not_connected') return raw
    return 'loading'
  } catch {
    return 'loading'
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

function ContactPicker({
  value,
  onChange,
  className,
}: {
  value: string
  onChange: (identifier: string) => void
  className?: string
}) {
  const [inputValue, setInputValue] = useState(value)
  const [suggestions, setSuggestions] = useState<ContactSuggestion[]>([])
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

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value
    setInputValue(v)
    onChange(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!v.trim()) {
      setSuggestions([])
      setOpen(false)
      return
    }
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.get<{ contacts: ContactSuggestion[] }>(
          `/imessage/contacts/search?q=${encodeURIComponent(v)}`
        )
        const list = res.contacts || []
        setSuggestions(list)
        setOpen(list.length > 0)
      } catch {
        setSuggestions([])
        setOpen(false)
      }
    }, 200)
  }

  const handleSelect = (c: ContactSuggestion) => {
    setInputValue(`${c.name} (${c.identifier})`)
    onChange(c.identifier)
    setSuggestions([])
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        placeholder="Name, phone number, or email"
        value={inputValue}
        onChange={handleChange}
        onFocus={() => suggestions.length > 0 && setOpen(true)}
        className={className}
        data-testid="contact-picker-input"
      />
      {open && suggestions.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-10 left-0 right-0 top-full mt-1 bg-slate-800 border border-slate-700 rounded-lg shadow-lg overflow-hidden"
        >
          {suggestions.map((s, i) => (
            <li key={i}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => handleSelect(s)}
                className="w-full text-left px-3 py-2 hover:bg-slate-700 text-sm transition-colors flex items-center gap-2"
              >
                <span className="font-medium text-white">{s.name}</span>
                <span className="text-slate-400 text-xs truncate">{s.identifier}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function IMessage() {
  const [connectionState, setConnectionState] = useState<ConnectionState>(() => readConnectionCache())
  const [statusReason, setStatusReason] = useState<string | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>(() => readCache())
  const [loading, setLoading] = useState<boolean>(() => readCache().length === 0)
  const [selectedChat, setSelectedChat] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const messagesScrollRef = useRef<HTMLDivElement | null>(null)
  const locallyReadRef = useRef<Set<number>>(new Set())

  // When a conversation opens or new messages load, jump to the bottom
  // so the user sees the latest message first (iMessage native behavior).
  useEffect(() => {
    if (messagesLoading) return
    const el = messagesScrollRef.current
    if (!el) return
    // Next frame so the message list is mounted before we scroll.
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight
    })
  }, [selectedChat, messages, messagesLoading])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
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
  const [peopleSearch, setPeopleSearch] = useState('')

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
        setStatusReason('Could not connect to the myOS backend. Make sure the backend is running, then refresh this page.')
        setConnectionState('not_connected')
        writeConnectionCache('not_connected')
      }
      setLoading(false)
    })()
    // Re-fetch when the window regains focus so messages read in iMessage.app
    // clear the unread count immediately rather than waiting for the cache TTL.
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

  const handleSearch = async () => {
    if (!searchQuery || searchQuery.length < 2) return
    setSearching(true)
    try {
      const res = await api.get<{ results: SearchResult[] }>(`/imessage/search?q=${encodeURIComponent(searchQuery)}`)
      setSearchResults(res.results || [])
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch()
  }

  const handleClearSearch = () => {
    setSearchQuery('')
    setSearchResults(null)
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
      // Refresh conversations to show the sent message
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

  const normalizePhone = (s: string) => s.replace(/\D/g, '')

  const peopleResults = useMemo(() => {
    if (!peopleSearch.trim()) return null
    const q = peopleSearch.toLowerCase()
    const qDigits = normalizePhone(peopleSearch)

    const matchedContacts = contacts.filter((c) =>
      c.name.toLowerCase().includes(q) ||
      c.phone_numbers.some((p) => p.toLowerCase().includes(q) || (qDigits.length >= 3 && normalizePhone(p).includes(qDigits))) ||
      c.emails.some((e) => e.toLowerCase().includes(q))
    )

    const matchedConversations = conversations.filter((c) =>
      (c.display_name || '').toLowerCase().includes(q) ||
      c.identifier.toLowerCase().includes(q) ||
      (qDigits.length >= 3 && normalizePhone(c.identifier).includes(qDigits))
    )

    return { contacts: matchedContacts, conversations: matchedConversations }
  }, [peopleSearch, contacts, conversations])

  const handleSelectContact = (contact: Contact) => {
    const nums = contact.phone_numbers.map(normalizePhone).filter(Boolean)
    const emails = contact.emails.map((e) => e.toLowerCase())
    const match = conversations.find((c) => {
      const id = c.identifier.toLowerCase()
      const idDigits = normalizePhone(c.identifier)
      return emails.includes(id) || nums.some((n) => idDigits === n || idDigits.includes(n))
    })
    setPeopleSearch('')
    if (match) {
      handleSelectChat(match.id)
    } else {
      setSendRecipient(contact.phone_numbers[0] || contact.emails[0] || '')
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-3 sm:p-4 rounded-xl'

  if (connectionState === 'loading') {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="People" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (connectionState === 'not_connected') {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="People" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="chat_bubble"
            accentColor="#22c55e"
            title="iMessage not available"
            description={statusReason || 'iMessage integration requires macOS with Full Disk Access enabled.'}
            primaryAction={
              statusReason?.includes('Full Disk Access') ? (
                <div className="bg-slate-800/50 p-4 rounded-xl text-sm text-slate-300 text-left w-full">
                  <p className="font-medium mb-2">How to enable:</p>
                  <ol className="list-decimal list-inside space-y-1 text-slate-400">
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
        </div>
      </div>
    )
  }


  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="People" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">People</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              {conversations.length} conversation{conversations.length !== 1 ? 's' : ''}
              {contacts.length > 0 && ` · ${contacts.length} contact${contacts.length !== 1 ? 's' : ''}`}
            </p>
          </div>
        </div>

        {/* Unified people search */}
        <div className="mb-4">
          <div className="relative">
            <Icon
              name="search"
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
            />
            <input
              type="text"
              placeholder="Search by name or number…"
              value={peopleSearch}
              onChange={(e) => setPeopleSearch(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-8 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
              data-testid="people-search-input"
            />
            {peopleSearch && (
              <button
                onClick={() => setPeopleSearch('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <Icon name="close" size={16} />
              </button>
            )}
          </div>
        </div>

        {/* People search results */}
        {peopleResults && (
          <div className={`${cardClass} mb-4`}>
            {peopleResults.contacts.length === 0 && peopleResults.conversations.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-4">No people found</p>
            ) : (
              <div className="divide-y divide-slate-800/60">
                {peopleResults.contacts.map((c, i) => (
                  <button
                    key={`contact-${i}`}
                    data-testid="people-search-contact-row"
                    onClick={() => handleSelectContact(c)}
                    className="w-full text-left px-3 py-3 rounded-lg hover:bg-slate-800/40 transition-colors flex items-center gap-3"
                  >
                    <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center shrink-0">
                      <span className="text-xs font-medium text-slate-300">
                        {(c.name || '?').charAt(0).toUpperCase()}
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-white truncate">{c.name}</p>
                      <p className="text-xs text-slate-400 truncate">
                        {c.phone_numbers[0] || c.emails[0] || ''}
                      </p>
                    </div>
                    <Icon name="chat_bubble_outline" size={14} className="text-slate-600 shrink-0" />
                  </button>
                ))}
                {peopleResults.conversations.map((c) => (
                  <button
                    key={`convo-${c.id}`}
                    data-testid="people-search-convo-row"
                    onClick={() => { handleSelectChat(c.id); setPeopleSearch('') }}
                    className="w-full text-left px-3 py-3 rounded-lg hover:bg-slate-800/40 transition-colors flex items-center gap-3"
                  >
                    <div className="w-7 h-7 rounded-full bg-blue-900/40 flex items-center justify-center shrink-0">
                      <Icon name="chat_bubble" size={14} className="text-blue-400" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-white truncate">
                        {c.display_name || c.identifier}
                      </p>
                      <p className="text-xs text-slate-400 truncate">{c.last_message_preview}</p>
                    </div>
                    {c.unread_count > 0 && (
                      <span className="bg-blue-500/20 text-blue-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0">
                        {c.unread_count}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Search results */}
        {searchResults !== null && (
          <div className={`${cardClass} mb-4`}>
            <div className="flex items-center gap-2 mb-3">
              <Icon name="search" className="text-blue-400" size={18} />
              <h2 className="text-base font-semibold">
                Search results ({searchResults.length})
              </h2>
            </div>
            {searchResults.length === 0 ? (
              <p className="text-sm text-slate-500 py-4 text-center">No messages found</p>
            ) : (
              <div className="divide-y divide-slate-800/60">
                {searchResults.map((r) => (
                  <button
                    key={r.message_id}
                    onClick={() => handleSelectChat(r.chat_id)}
                    className="w-full text-left px-3 py-3 rounded-lg hover:bg-slate-800/40 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-200 truncate">
                        {r.chat_display_name}
                      </span>
                      <span className="text-xs text-slate-500 shrink-0">
                        {formatDate(r.date)}
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 mt-0.5 truncate">{r.text}</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* New message composer */}
        <div className={`${cardClass} mb-4`}>
          <div className="flex items-center gap-2 mb-3">
            <Icon name="edit" className="text-blue-400" size={18} />
            <h2 className="text-base font-semibold">Send a message</h2>
          </div>
          <div className="space-y-2">
            <ContactPicker
              value={sendRecipient}
              onChange={setSendRecipient}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
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
                className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
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
            <Icon name="chat_bubble" className="text-blue-400" size={18} />
            <h2 className="text-base font-semibold">Conversations</h2>
          </div>

          {loading ? (
            <LoadingState variant="spinner" message="Loading conversations..." />
          ) : conversations.length === 0 ? (
            <EmptyState icon="chat_bubble_outline" title="No conversations here yet." />
          ) : (
            <div className="divide-y divide-slate-800/60">
              {conversations.map((convo) => {
                const isSelected = selectedChat === convo.id
                return (
                  <div key={convo.id} className="py-1">
                    <button
                      onClick={() => handleSelectChat(convo.id)}
                      aria-expanded={isSelected}
                      className="w-full text-left px-3 py-3 rounded-lg transition-colors hover:bg-slate-800/40"
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
                            <p className={`text-sm truncate ${convo.unread_count > 0 ? 'font-semibold text-white' : 'text-slate-300'}`}>
                              {convo.display_name || convo.identifier}
                            </p>
                            <span className="text-xs text-slate-500 shrink-0">
                              {formatDate(convo.last_message_date)}
                            </span>
                          </div>
                          <p className="text-xs text-slate-500 truncate mt-0.5">
                            {convo.last_message_preview}
                          </p>
                        </div>
                        <div className="shrink-0 mt-1 flex items-center gap-2">
                          {convo.unread_count > 0 && (
                            <span className="bg-blue-500/20 text-blue-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
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
                          <div className="mb-3 p-2.5 bg-slate-800/60 border border-slate-700 rounded-xl">
                            {saveContactChatId === convo.id ? (
                              <div className="flex flex-col gap-2">
                                <p className="text-xs text-slate-400">What name should we use for {convo.identifier}?</p>
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
                                    className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
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
                                    className="px-2 py-1.5 text-slate-400 hover:text-slate-200 rounded-lg text-sm transition-colors"
                                  >
                                    <Icon name="close" size={14} />
                                  </button>
                                </div>
                                {saveContactError && (
                                  <p className="text-xs text-red-400">{saveContactError}</p>
                                )}
                              </div>
                            ) : (
                              <button
                                data-testid="save-to-contacts-btn"
                                onClick={() => { setSaveContactChatId(convo.id); setSaveContactName(''); setSaveContactError(null) }}
                                className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 transition-colors"
                              >
                                <Icon name="person_add" size={14} />
                                Save to Contacts
                              </button>
                            )}
                          </div>
                        )}

                        <div
                          ref={messagesScrollRef}
                          className="bg-slate-900 border border-slate-800 rounded-xl p-4 max-h-96 overflow-y-auto"
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
                                        : 'bg-slate-800 text-slate-200'
                                    }`}
                                  >
                                    {msg.attachments?.filter(a => a.mime_type?.startsWith('image/')).map((att, i) => (
                                      <img
                                        key={i}
                                        src={`/api/imessage/attachment?path=${encodeURIComponent(att.filename)}`}
                                        alt={att.transfer_name || 'image'}
                                        className="max-w-full rounded-lg mb-1"
                                        loading="lazy"
                                      />
                                    ))}
                                    {msg.text && <p className="whitespace-pre-wrap">{msg.text}</p>}
                                    <p className={`text-[10px] mt-1 ${msg.is_from_me ? 'text-blue-200' : 'text-slate-500'}`}>
                                      {formatDate(msg.date)}
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
                              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
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
      </div>
    </div>
  )
}
