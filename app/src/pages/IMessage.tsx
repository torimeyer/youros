import { useState, useEffect, useCallback } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { api } from '../lib/api'

interface Conversation {
  id: number
  identifier: string
  display_name: string
  service: string
  last_message_date: string
  last_message_preview: string
  message_count: number
  unread_count: number
}

interface Message {
  id: number
  text: string
  date: string
  is_from_me: boolean
  is_read: boolean
  sender: string
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

interface StatusResponse {
  available: boolean
  reason: string | null
}

// Seed from localStorage so the page paints immediately
const IMESSAGE_CACHE_KEY = 'myos.imessageCache.v1'

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

export default function IMessage() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>(() => readCache())
  const [loading, setLoading] = useState<boolean>(() => readCache().length === 0)
  const [selectedChat, setSelectedChat] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [sendRecipient, setSendRecipient] = useState('')
  const [sendText, setSendText] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [sendSuccess, setSendSuccess] = useState(false)
  const [replyText, setReplyText] = useState('')

  const fetchConversations = useCallback(async () => {
    try {
      const res = await api.get<{ conversations: Conversation[] }>('/imessage/conversations')
      const fetched = res.conversations || []
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
        setStatus(s)
        if (s.available) {
          await fetchConversations()
        }
      } catch {
        setStatus({ available: false, reason: 'Could not connect to the myOS backend. Make sure the backend is running, then refresh this page.' })
      }
      setLoading(false)
    })()
  }, [fetchConversations])

  const handleSelectChat = (chatId: number) => {
    if (selectedChat === chatId) {
      setSelectedChat(null)
      setMessages([])
      return
    }
    setSelectedChat(chatId)
    setReplyText('')
    fetchMessages(chatId)
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

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-3 sm:p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="iMessage" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 flex items-center gap-2 text-slate-400">
          <Icon name="progress_activity" size={20} className="animate-spin" />
          Loading...
        </div>
      </div>
    )
  }

  if (!status?.available) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <TopBar title="iMessage" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 max-w-md mx-auto">
          <div className="bg-slate-900/40 border border-slate-800 p-5 sm:p-8 rounded-2xl">
            <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center mb-4">
              <Icon name="chat_bubble" className="text-blue-400" size={24} />
            </div>
            <h2 className="text-xl font-semibold mb-2">iMessage not available</h2>
            <p className="text-slate-400 mb-4">
              {status?.reason || 'iMessage integration requires macOS with Full Disk Access enabled.'}
            </p>
            {status?.reason?.includes('Full Disk Access') && (
              <div className="bg-slate-800/50 p-4 rounded-lg text-sm text-slate-300">
                <p className="font-medium mb-2">How to enable:</p>
                <ol className="list-decimal list-inside space-y-1 text-slate-400">
                  <li>Open System Settings</li>
                  <li>Go to Privacy & Security</li>
                  <li>Click Full Disk Access</li>
                  <li>Enable access for your terminal app (Terminal, iTerm2, etc.)</li>
                  <li>Restart the terminal and try again</li>
                </ol>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  // Selected conversation detail view
  const selectedConvo = conversations.find((c) => c.id === selectedChat)

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="iMessage" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">iMessage</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              {conversations.length} conversation{conversations.length !== 1 ? 's' : ''}
            </p>
          </div>
        </div>

        {/* Search bar */}
        <div className="mb-4 flex gap-2">
          <div className="flex-1 relative">
            <input
              type="text"
              placeholder="Search messages..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            {searchResults !== null && (
              <button
                onClick={handleClearSearch}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <Icon name="close" size={16} />
              </button>
            )}
          </div>
          <button
            onClick={handleSearch}
            disabled={searching || searchQuery.length < 2}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            {searching ? (
              <Icon name="progress_activity" size={16} className="animate-spin" />
            ) : (
              <Icon name="search" size={16} />
            )}
          </button>
        </div>

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
              <p className="text-sm text-slate-500 py-4 text-center">No messages found.</p>
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
            <input
              type="text"
              placeholder="Phone number or email"
              value={sendRecipient}
              onChange={(e) => setSendRecipient(e.target.value)}
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
              <p className="text-sm text-red-400">{sendError}</p>
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

          {conversations.length === 0 ? (
            <div className="text-center py-8 text-slate-500">
              <Icon name="chat_bubble_outline" size={36} className="mb-2 mx-auto opacity-40" />
              <p>No conversations found.</p>
            </div>
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
                        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 max-h-96 overflow-y-auto">
                          {messagesLoading ? (
                            <div className="flex items-center gap-2 text-slate-400 py-4 justify-center">
                              <Icon name="progress_activity" size={16} className="animate-spin" />
                              Loading messages...
                            </div>
                          ) : messages.length === 0 ? (
                            <p className="text-sm text-slate-500 text-center py-4">No messages.</p>
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
                                    <p className="whitespace-pre-wrap">{msg.text}</p>
                                    <p className={`text-[10px] mt-1 ${msg.is_from_me ? 'text-blue-200' : 'text-slate-500'}`}>
                                      {formatDate(msg.date)}
                                    </p>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Quick reply within conversation */}
                          {selectedConvo && (
                            <div className="mt-3 flex gap-2">
                              <input
                                type="text"
                                placeholder="Reply..."
                                value={replyText}
                                onChange={(e) => setReplyText(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' && !e.shiftKey && selectedConvo) {
                                    e.preventDefault()
                                    handleSend(selectedConvo.identifier, replyText)
                                  }
                                }}
                                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                              />
                              <button
                                onClick={() => handleSend(selectedConvo.identifier, replyText)}
                                disabled={sending || !replyText}
                                className="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors disabled:opacity-50"
                              >
                                <Icon name="send" size={16} />
                              </button>
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
