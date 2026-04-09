import { useState, useEffect, useRef, useCallback } from 'react'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { useWebSocket } from '../hooks/useWebSocket'
import { renderMarkdown, renderTextWithMarkdown } from '../lib/markdown'
import { api } from '../lib/api'

// Local cache key. The server is the source of truth for chat history.
// We still mirror to localStorage so the very first paint after a hard
// refresh shows messages instantly while the server fetch is in flight.
const CHAT_CACHE_KEY = 'myos-chat-messages'

interface ChatHistoryPayload {
  tabs: ChatTab[]
  active_tab_id: string
}

const MODEL_COLORS: Record<string, string> = {
  claude: 'text-blue-400',
  gemini: 'text-emerald-400',
}

const MODEL_BG: Record<string, string> = {
  claude: 'bg-blue-500/10 border-blue-500/30',
  gemini: 'bg-emerald-500/10 border-emerald-500/30',
}

const TOOL_LABELS: Record<string, string> = {
  read_file: 'Read file',
  write_file: 'Write file',
  edit_file: 'Edit file',
  run_command: 'Run command',
  list_directory: 'List directory',
  search_files: 'Search files',
  list_tasks: 'List tasks',
  create_task: 'Create task',
  close_task: 'Close task',
  check_agents: 'Check agents',
  spawn_agent: 'Spawn agent',
  web_search: 'Web search',
  web_fetch: 'Fetch page',
  git_status: 'Git status',
  git_diff: 'Git diff',
  git_commit: 'Git commit',
}

const REACTION_EMOJIS = ['👍', '❤️', '😂', '😮', '🔥', '👎']

interface ToolCall {
  id: string
  tool: string
  input: Record<string, unknown>
  result?: string
  isMcp?: boolean
  mcpServer?: string
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  model?: string
  toolCalls?: ToolCall[]
  replyTo?: string
  gifUrl?: string
  imageUrl?: string
  reactions?: Record<string, number>
}

interface GiphyResult {
  id: string
  url: string
  preview: string
  title: string
}

export interface ChatTab {
  id: string
  name: string
  messages: Message[]
  /** ISO timestamp of the last message or tab creation. Used to prune old tabs. */
  updatedAt?: string
}

function genId(): string {
  return crypto.randomUUID()
}

function deriveTabName(messages: Message[]): string {
  const firstUserMsg = messages.find(m => m.role === 'user' && m.content.trim())
  if (!firstUserMsg) return 'New Chat'
  const text = firstUserMsg.content.trim()
  return text.length > 24 ? text.slice(0, 24) + '...' : text
}

function ToolCallBlock({ call }: { call: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const label = call.isMcp
    ? `${call.mcpServer}: ${call.tool}`
    : (TOOL_LABELS[call.tool] ?? call.tool)

  let summary = ''
  if (call.input.path) summary = String(call.input.path)
  else if (call.input.command) summary = String(call.input.command)
  else if (call.input.pattern) summary = String(call.input.pattern)
  else if (call.input.title) summary = String(call.input.title)
  else if (call.input.task_id) summary = String(call.input.task_id)

  const labelColor = call.isMcp ? 'text-purple-400' : 'text-amber-400'

  return (
    <div className="my-1.5 border border-slate-700 rounded-lg overflow-hidden bg-slate-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-300 hover:bg-slate-800/50 transition-colors"
      >
        <Icon name={expanded ? 'expand_more' : 'chevron_right'} className="text-sm" />
        <span className={`font-medium ${labelColor}`}>{label}</span>
        {summary && (
          <span className="text-slate-500 truncate max-w-[200px]">{summary}</span>
        )}
        {call.result !== undefined && (
          <Icon name="check_circle" className="text-green-500 text-sm ml-auto" />
        )}
        {call.result === undefined && (
          <span className="ml-auto inline-block w-3 h-3 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
        )}
      </button>
      {expanded && call.result !== undefined && (
        <div className="border-t border-slate-700 px-3 py-2 max-h-48 overflow-y-auto">
          <pre className="text-[11px] text-slate-400 whitespace-pre-wrap font-mono leading-relaxed">
            {call.result}
          </pre>
        </div>
      )}
    </div>
  )
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-2 py-1 text-slate-400 text-xs">
      <span className="inline-flex items-center gap-1">
        <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
        <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
        <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
      </span>
      <span>Thinking</span>
    </span>
  )
}

function CollapsibleText({ text, isLast, streaming }: { text: string; isLast: boolean; streaming: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const isLong = text.length > 300
  // While streaming the last message, only show last ~200 chars to reduce noise
  if (streaming && isLast && isLong && !expanded) {
    const lastChunk = text.slice(-200)
    const breakPoint = lastChunk.indexOf('. ')
    const display = breakPoint > 0 ? lastChunk.slice(breakPoint + 2) : lastChunk
    return (
      <div>
        <button onClick={() => setExpanded(true)} className="text-[10px] text-blue-400 hover:text-blue-300 mb-1">
          Show full response ({text.length} chars)
        </button>
        <div>{renderMarkdown(display)}</div>
      </div>
    )
  }
  if (!streaming && isLong && !expanded) {
    // After streaming is done, show first 300 chars with expand option
    return (
      <div>
        <div>{renderMarkdown(text.slice(0, 300))}...</div>
        <button onClick={() => setExpanded(true)} className="text-[10px] text-blue-400 hover:text-blue-300 mt-1">
          Show more
        </button>
      </div>
    )
  }
  return (
    <div>
      {renderMarkdown(text)}
      {isLong && expanded && (
        <button onClick={() => setExpanded(false)} className="block text-[10px] text-blue-400 hover:text-blue-300 mt-1">
          Show less
        </button>
      )}
    </div>
  )
}

function ReplyPreview({ message, onClick }: { message: Message | undefined; onClick?: () => void }) {
  if (!message) return null
  const sender = message.role === 'user' ? 'You' : (message.model || 'Assistant')
  const preview = message.gifUrl
    ? 'GIF'
    : message.content.length > 50
      ? message.content.slice(0, 50) + '...'
      : message.content
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 mb-1 pl-2 border-l-2 border-blue-500/50 text-left w-full"
    >
      <span className="text-[10px] font-medium text-blue-400">{sender}</span>
      <span className="text-[10px] text-slate-500 truncate">{preview}</span>
    </button>
  )
}

function GiphyPicker({ initialSearch, onSelect, onClose }: {
  initialSearch?: string
  onSelect: (url: string) => void
  onClose: () => void
}) {
  const [search, setSearch] = useState(initialSearch || '')
  const [results, setResults] = useState<GiphyResult[]>([])
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!search.trim()) { setResults([]); return }
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const resp = await fetch(`/api/giphy/search?q=${encodeURIComponent(search.trim())}&limit=12`)
        const data = await resp.json()
        setResults(data)
      } catch {
        setResults([])
      }
      setLoading(false)
    }, 350)
    return () => clearTimeout(timer)
  }, [search])

  return (
    <div className="border-t border-slate-800 bg-slate-900/95 p-3">
      <div className="flex items-center gap-2 mb-2">
        <input
          ref={inputRef}
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') onClose() }}
          className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-300 outline-none focus:ring-2 focus:ring-blue-500/50"
          placeholder="Search GIFs..."
        />
        <button onClick={onClose} className="p-1 text-slate-400 hover:text-white transition-colors">
          <Icon name="close" className="text-sm" />
        </button>
      </div>
      {loading && (
        <div className="flex justify-center py-4">
          <span className="inline-block w-5 h-5 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
        </div>
      )}
      {!loading && results.length > 0 && (
        <div className="grid grid-cols-3 gap-1.5 max-h-52 overflow-y-auto">
          {results.map(gif => (
            <button
              key={gif.id}
              onClick={() => onSelect(gif.url)}
              className="rounded-lg overflow-hidden hover:ring-2 hover:ring-blue-500 transition-all"
              title={gif.title}
            >
              <img src={gif.preview || gif.url} alt={gif.title} className="w-full h-24 object-cover" loading="lazy" />
            </button>
          ))}
        </div>
      )}
      {!loading && search.trim() && results.length === 0 && (
        <p className="text-center text-slate-500 text-xs py-3">No GIFs found</p>
      )}
      {!search.trim() && !loading && (
        <p className="text-center text-slate-600 text-xs py-3">Type to search for GIFs</p>
      )}
      <p className="text-[10px] text-slate-600 mt-2 text-right">Powered by GIPHY</p>
    </div>
  )
}

export function ChatPanel() {
  const { chatOpen, toggleChat, chatWidth, setChatWidth, isResizing, setIsResizing, defaultChatModel } = useAppStore()

  // --- Tab state ---
  // First paint reads cached messages from localStorage so the panel is
  // never blank, then hydrateChatHistory below fetches the authoritative
  // copy from the server and replaces it.
  const [tabs, setTabs] = useState<ChatTab[]>(() => {
    try {
      const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(CHAT_CACHE_KEY) : null
      const msgs: Message[] = saved ? JSON.parse(saved) : []
      const firstTab: ChatTab = { id: genId(), name: deriveTabName(msgs), messages: msgs }
      return [firstTab]
    } catch {
      return [{ id: genId(), name: 'New Chat', messages: [] }]
    }
  })
  const [activeTabId, setActiveTabId] = useState<string>(() => tabs[0]?.id ?? '')
  const [historyHydrated, setHistoryHydrated] = useState(false)

  const activeTab = tabs.find(t => t.id === activeTabId) ?? tabs[0]
  const messages = activeTab?.messages ?? []

  const setMessages = useCallback((updater: Message[] | ((prev: Message[]) => Message[])) => {
    setTabs(prev => prev.map(tab => {
      if (tab.id !== activeTabId) return tab
      const newMessages = typeof updater === 'function' ? updater(tab.messages) : updater
      const newName = tab.name === 'New Chat' ? deriveTabName(newMessages) : tab.name
      return { ...tab, messages: newMessages, name: newName, updatedAt: new Date().toISOString() }
    }))
  }, [activeTabId])

  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [currentModel, setCurrentModel] = useState<string | null>(null)
  const [toolsEnabled, setToolsEnabled] = useState(true)
  const [activeTemplate, setActiveTemplate] = useState<{ name: string; description?: string } | null>(null)
  // Which pathway is powering this response: the local Claude subscription
  // program or the Anthropic API. Updated from the backend_active event.
  const [activeBackend, setActiveBackend] = useState<{ name: string; label: string } | null>(null)
  // Multi-AI conversation status. When two models are talking to each
  // other, the backend emits multi_ai_status events bracketed by
  // multi_ai_turn_start and multi_ai_turn_end so we can render one bubble
  // per turn with a live thinking pill above the latest assistant row.
  // Null whenever no multi-AI exchange is in flight.
  const [multiAiStatus, setMultiAiStatus] = useState<{
    phase: 'starting' | 'thinking' | 'speaking' | 'complete'
    model?: string
    round?: number
    models?: string[]
    rounds?: number
  } | null>(null)
  // Id of the assistant bubble currently receiving streaming tokens.
  // During a single-model reply this stays null so the token handler
  // falls back to the append-to-last-assistant path. During a multi-AI
  // exchange this points at the freshly created bubble for the model
  // that is currently speaking, and is updated on every
  // multi_ai_turn_start so tokens never leak into the wrong bubble.
  // Held in a ref so updating it does not re-run the lastMessage
  // effect, which would otherwise loop on the same event.
  const currentBubbleIdRef = useRef<string | null>(null)
  const [replyingTo, setReplyingTo] = useState<string | null>(null)
  const [showGiphy, setShowGiphy] = useState(false)
  const [giphyInitialSearch, setGiphyInitialSearch] = useState('')
  const [pendingImage, setPendingImage] = useState<string | null>(null)
  const [commandHistory, setCommandHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const lastEscRef = useRef(0)
  const lastUpRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const { connect, send, lastMessage, isConnected } = useWebSocket('/ws/chat')

  const findMessage = useCallback((id: string) => messages.find(m => m.id === id), [messages])

  useEffect(() => {
    if (chatOpen && !isConnected) {
      connect()
    }
  }, [chatOpen, isConnected, connect])

  useEffect(() => {
    if (!lastMessage) return

    if (lastMessage.type === 'model_label') {
      setCurrentModel((lastMessage.data as string) ?? null)
    } else if (lastMessage.type === 'template_matched') {
      const data = lastMessage.data as unknown as { name: string; description?: string }
      if (data?.name) {
        setActiveTemplate({ name: data.name, description: data.description })
      }
    } else if (lastMessage.type === 'backend_active') {
      const data = lastMessage.data as unknown as { name: string; label: string }
      if (data?.name) {
        setActiveBackend({ name: data.name, label: data.label })
      }
    } else if (lastMessage.type === 'thinking') {
      // Ensure streaming state is active so ThinkingDots shows
      setIsStreaming(true)
    } else if (lastMessage.type === 'token') {
      setMessages(prev => {
        // When a multi-AI turn is active, route the token into the
        // bubble for the speaker so the tokens never bleed into a
        // previous bubble. Otherwise fall back to the legacy
        // append-to-last-assistant path the single-model flow uses.
        const bubbleId = currentBubbleIdRef.current
        if (bubbleId) {
          return prev.map(m =>
            m.id === bubbleId && m.role === 'assistant'
              ? { ...m, content: m.content + (lastMessage.data as string) }
              : m,
          )
        }
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, content: last.content + lastMessage.data }
        }
        return updated
      })
    } else if (lastMessage.type === 'multi_ai_status') {
      // Drives the live thinking pill above the latest assistant row.
      // The phase field tells us which copy to render. We also clear
      // the pill on the complete phase so it never sticks after the
      // exchange wraps up.
      const data = lastMessage.data as unknown as {
        phase: 'starting' | 'thinking' | 'speaking' | 'complete'
        model?: string
        round?: number
        models?: string[]
        rounds?: number
      }
      if (!data) return
      if (data.phase === 'complete') {
        setMultiAiStatus(null)
      } else {
        // Preserve the models and rounds from the starting event so
        // later thinking phases can still render the total round count.
        setMultiAiStatus(prev => {
          const carriedModels = data.models ?? prev?.models
          const carriedRounds = data.rounds ?? prev?.rounds
          return { ...data, models: carriedModels, rounds: carriedRounds }
        })
      }
    } else if (lastMessage.type === 'multi_ai_turn_start') {
      // Open a brand new assistant bubble for this turn and switch the
      // current bubble pointer so subsequent tokens land in it. If the
      // last bubble is the empty placeholder pushed by sendMessage,
      // reuse it for the first turn so the panel does not show a
      // dangling empty bubble above the conversation.
      const data = lastMessage.data as unknown as { model: string; round: number }
      if (!data?.model) return
      const newBubbleId = genId()
      setMessages(prev => {
        const last = prev[prev.length - 1]
        if (
          last &&
          last.role === 'assistant' &&
          !last.content &&
          !last.toolCalls?.length
        ) {
          const reused = { ...last, id: newBubbleId, model: data.model }
          return [...prev.slice(0, -1), reused]
        }
        return [
          ...prev,
          { id: newBubbleId, role: 'assistant', content: '', model: data.model },
        ]
      })
      currentBubbleIdRef.current = newBubbleId
      setIsStreaming(true)
    } else if (lastMessage.type === 'multi_ai_turn_end') {
      // Close the current bubble. The bubble stays in the list, just
      // no longer accepts streaming tokens. A late stray token after
      // turn_end is dropped, which is the safe behavior.
      currentBubbleIdRef.current = null
    } else if (lastMessage.type === 'tool_use') {
      const data = lastMessage.data as unknown as { tool: string; input: Record<string, unknown>; id: string }
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          const calls = [...(last.toolCalls ?? [])]
          calls.push({ id: data.id, tool: data.tool, input: data.input })
          updated[updated.length - 1] = { ...last, toolCalls: calls }
        }
        return updated
      })
    } else if (lastMessage.type === 'mcp_tool_use') {
      const data = lastMessage.data as unknown as { tool: string; server: string; input: Record<string, unknown>; id: string }
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          const calls = [...(last.toolCalls ?? [])]
          calls.push({ id: data.id, tool: data.tool, input: data.input, isMcp: true, mcpServer: data.server })
          updated[updated.length - 1] = { ...last, toolCalls: calls }
        }
        return updated
      })
    } else if (lastMessage.type === 'tool_result') {
      const data = lastMessage.data as unknown as { id: string; result: string }
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant' && last.toolCalls) {
          const calls = last.toolCalls.map(tc =>
            tc.id === data.id ? { ...tc, result: data.result } : tc,
          )
          updated[updated.length - 1] = { ...last, toolCalls: calls }
        }
        return updated
      })
    } else if (lastMessage.type === 'mcp_tool_result') {
      const data = lastMessage.data as unknown as { id: string; result: string; is_error: boolean }
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant' && last.toolCalls) {
          const calls = last.toolCalls.map(tc =>
            tc.id === data.id ? { ...tc, result: data.is_error ? `Error: ${data.result}` : data.result } : tc,
          )
          updated[updated.length - 1] = { ...last, toolCalls: calls }
        }
        return updated
      })
    } else if (lastMessage.type === 'model_boundary') {
      setMessages(prev => {
        const last = prev[prev.length - 1]
        // If the last message is already an empty assistant placeholder (created by sendMessage),
        // don't push a duplicate. Just keep the existing one.
        if (last && last.role === 'assistant' && !last.content && !last.toolCalls?.length) {
          return prev
        }
        return [...prev, { id: genId(), role: 'assistant', content: '', model: '' }]
      })
    } else if (lastMessage.type === 'done') {
      if (currentModel) {
        setMessages(prev => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last && last.role === 'assistant') {
            updated[updated.length - 1] = { ...last, model: currentModel }
          }
          return updated
        })
      }
      setIsStreaming(false)
      setCurrentModel(null)
      // Defensive cleanup. The complete phase usually arrives just
      // before done, but if the backend ever drops it the pill must
      // still go away when the stream ends.
      setMultiAiStatus(null)
      currentBubbleIdRef.current = null
    } else if (lastMessage.type === 'error') {
      setIsStreaming(false)
      setCurrentModel(null)
      setMultiAiStatus(null)
      currentBubbleIdRef.current = null
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, content: `Error: ${lastMessage.data}` }
        }
        return updated
      })
    }
  }, [lastMessage, currentModel])

  // Hydrate chat history from the server on first mount. The server is
  // the source of truth, so anything it returns replaces what was in the
  // localStorage cache. This makes chat history survive cache clears and
  // follow the user across devices.
  useEffect(() => {
    let cancelled = false
    api
      .get<ChatHistoryPayload>('/chat/history')
      .then((data) => {
        if (cancelled) return
        const serverTabs = Array.isArray(data?.tabs) ? data.tabs : []
        if (serverTabs.length > 0) {
          setTabs(serverTabs)
          const activeId = data.active_tab_id && serverTabs.some((t) => t.id === data.active_tab_id)
            ? data.active_tab_id
            : serverTabs[0].id
          setActiveTabId(activeId)
        }
      })
      .catch(() => {
        // Server unreachable. Keep whatever the localStorage cache gave us.
      })
      .finally(() => {
        if (!cancelled) setHistoryHydrated(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Persist tabs. Cache the active tab in localStorage for fast first
  // paint, and write the full set of tabs to the server (debounced) so it
  // survives cache clears and device switches. We only start writing to
  // the server after the initial hydrate completes so we never overwrite
  // server state with stale local state.
  useEffect(() => {
    try {
      const toSave = messages.map((m) => (m.imageUrl ? { ...m, imageUrl: undefined } : m))
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(CHAT_CACHE_KEY, JSON.stringify(toSave))
      }
    } catch {
      /* quota exceeded, skip */
    }
  }, [messages])

  useEffect(() => {
    if (!historyHydrated) return
    const handle = setTimeout(() => {
      // Strip base64 images before sending so we never blow past the
      // server payload limit.
      let tabsToSave = tabs.map((tab) => ({
        ...tab,
        messages: tab.messages.map((m) => (m.imageUrl ? { ...m, imageUrl: undefined } : m)),
      }))
      // Prune tabs older than 30 days.
      const cutoff = new Date()
      cutoff.setDate(cutoff.getDate() - 30)
      tabsToSave = tabsToSave.filter((tab) => {
        if (!tab.updatedAt) return true
        return new Date(tab.updatedAt) >= cutoff
      })
      // Keep at most 10 tabs. If the active tab would be cut, preserve it.
      if (tabsToSave.length > 10) {
        const sorted = [...tabsToSave].sort((a, b) => {
          const ta = a.updatedAt ? new Date(a.updatedAt).getTime() : 0
          const tb = b.updatedAt ? new Date(b.updatedAt).getTime() : 0
          return tb - ta
        })
        const kept = sorted.slice(0, 10)
        if (!kept.find((t) => t.id === activeTabId)) {
          const active = tabsToSave.find((t) => t.id === activeTabId)
          if (active) kept[kept.length - 1] = active
        }
        tabsToSave = kept
      }
      api
        .put('/chat/history', { tabs: tabsToSave, active_tab_id: activeTabId })
        .catch(() => {
          /* network down, will retry on next change */
        })
    }, 500)
    return () => clearTimeout(handle)
  }, [tabs, activeTabId, historyHydrated])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when starting a reply
  useEffect(() => {
    if (replyingTo) inputRef.current?.focus()
  }, [replyingTo])

  // Resize handling. We throttle mouse move writes to the store with
  // requestAnimationFrame so a fast drag does not flood Zustand with
  // dozens of updates per frame. The store still clamps to the current
  // [min, viewport - reserved] range so the chat never crosses over the
  // sidebar or shrinks below a usable width.
  const handleMouseDown = useCallback(() => {
    setIsResizing(true)
  }, [setIsResizing])

  useEffect(() => {
    if (!isResizing) return

    let pendingWidth: number | null = null
    let rafId: number | null = null

    const flush = () => {
      rafId = null
      if (pendingWidth !== null) {
        setChatWidth(pendingWidth)
        pendingWidth = null
      }
    }

    const handleMouseMove = (e: MouseEvent) => {
      pendingWidth = window.innerWidth - e.clientX
      if (rafId === null) {
        rafId = typeof requestAnimationFrame !== 'undefined'
          ? requestAnimationFrame(flush)
          : (setTimeout(flush, 16) as unknown as number)
      }
    }

    const handleMouseUp = () => {
      if (pendingWidth !== null) {
        setChatWidth(pendingWidth)
        pendingWidth = null
      }
      setIsResizing(false)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      if (rafId !== null) {
        if (typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(rafId)
        } else {
          clearTimeout(rafId)
        }
      }
    }
  }, [isResizing, setChatWidth, setIsResizing])

  // Re-clamp the chat width when the viewport shrinks. Without this a
  // user could resize the chat to 1200px on a wide monitor, unplug the
  // monitor, and end up on a 1024px laptop where the chat now hides the
  // whole main content area.
  useEffect(() => {
    const handleWindowResize = () => {
      setChatWidth(chatWidth)
    }
    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [chatWidth, setChatWidth])

  if (!chatOpen) return null

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData.items
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        e.preventDefault()
        const file = items[i].getAsFile()
        if (!file) return
        const reader = new FileReader()
        reader.onload = () => {
          setPendingImage(reader.result as string)
        }
        reader.readAsDataURL(file)
        return
      }
    }
  }

  const sendMessage = (text: string) => {
    // Handle /giphy command
    if (text.trim().toLowerCase().startsWith('/giphy')) {
      const searchTerm = text.trim().slice(6).trim()
      setGiphyInitialSearch(searchTerm)
      setShowGiphy(true)
      return
    }

    if (!text.trim() && !pendingImage) return
    // Clear any prior template badge so the next response shows its own.
    setActiveTemplate(null)
    const userMessage: Message = {
      id: genId(),
      role: 'user',
      content: text.trim(),
      replyTo: replyingTo || undefined,
      imageUrl: pendingImage || undefined,
    }

    const mentionMatch = text.match(/@(\w+)/i)
    const detectedModel = mentionMatch ? mentionMatch[1].toLowerCase() : defaultChatModel

    const assistantMessage: Message = { id: genId(), role: 'assistant', content: '', model: detectedModel }
    const updatedMessages = [...messages, userMessage]
    setMessages([...updatedMessages, assistantMessage])
    setIsStreaming(true)
    setCurrentModel(detectedModel)
    setReplyingTo(null)
    setPendingImage(null)

    // Build the messages payload
    const apiMessages = updatedMessages.map(m => {
      if (m.imageUrl) return { role: m.role, content: m.content || '[image]', image: m.imageUrl }
      if (m.gifUrl) return { role: m.role, content: `[gif:${m.gifUrl}]` }
      return { role: m.role, content: m.content || '' }
    })
    if (userMessage.replyTo) {
      const repliedMsg = messages.find(m => m.id === userMessage.replyTo)
      if (repliedMsg) {
        const replyContent = repliedMsg.gifUrl ? '[GIF]' : repliedMsg.content
        const sender = repliedMsg.role === 'user' ? 'the user' : (repliedMsg.model || 'the assistant')
        const lastIdx = apiMessages.length - 1
        apiMessages[lastIdx] = {
          ...apiMessages[lastIdx],
          content: `[Replying to ${sender}: "${replyContent.slice(0, 100)}"]\n\n${apiMessages[lastIdx].content}`,
        }
      }
    }

    send({
      model: `@${defaultChatModel}`,
      messages: apiMessages,
      tools: toolsEnabled,
    })
  }

  const sendGif = (gifUrl: string) => {
    const userMessage: Message = {
      id: genId(),
      role: 'user',
      content: '',
      gifUrl,
      replyTo: replyingTo || undefined,
    }
    const assistantMessage: Message = { id: genId(), role: 'assistant', content: '', model: defaultChatModel }
    const updatedMessages = [...messages, userMessage]
    setMessages([...updatedMessages, assistantMessage])
    setIsStreaming(true)
    setCurrentModel(defaultChatModel)
    setShowGiphy(false)
    setReplyingTo(null)

    // Send messages with GIF URLs so the AI can see them
    const apiMessages = updatedMessages.map(m => ({
      role: m.role,
      content: m.gifUrl ? `[gif:${m.gifUrl}]` : (m.content || ''),
    }))

    send({
      model: `@${defaultChatModel}`,
      messages: apiMessages,
      tools: toolsEnabled,
    })
  }

  const handleSend = () => {
    if (!input.trim() && !pendingImage) return
    if (input.trim()) {
      setCommandHistory(prev => [...prev, input.trim()])
      setHistoryIndex(-1)
    }
    sendMessage(input)
    setInput('')
  }

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSend()
      return
    }

    if (e.key === 'Escape') {
      const now = Date.now()
      if (now - lastEscRef.current < 500) {
        setInput('')
        setPendingImage(null)
        setReplyingTo(null)
        lastEscRef.current = 0
      } else {
        lastEscRef.current = now
      }
      return
    }

    if (e.key === 'ArrowUp' && !input) {
      const now = Date.now()
      if (now - lastUpRef.current < 500) {
        if (commandHistory.length > 0) {
          const idx = historyIndex === -1 ? commandHistory.length - 1 : Math.max(0, historyIndex - 1)
          setHistoryIndex(idx)
          setInput(commandHistory[idx])
        }
        lastUpRef.current = 0
      } else {
        lastUpRef.current = now
      }
      return
    }

    if (e.key === 'ArrowDown' && historyIndex >= 0) {
      if (historyIndex < commandHistory.length - 1) {
        const idx = historyIndex + 1
        setHistoryIndex(idx)
        setInput(commandHistory[idx])
      } else {
        setHistoryIndex(-1)
        setInput('')
      }
      return
    }
  }

  const handleNewConversation = () => {
    const newTab: ChatTab = { id: genId(), name: 'New Chat', messages: [], updatedAt: new Date().toISOString() }
    setTabs(prev => [...prev, newTab])
    setActiveTabId(newTab.id)
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
  }

  const handleClearHistory = () => {
    if (!window.confirm('Clear all chat history? This cannot be undone.')) return
    const freshTab: ChatTab = { id: genId(), name: 'New Chat', messages: [], updatedAt: new Date().toISOString() }
    setTabs([freshTab])
    setActiveTabId(freshTab.id)
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
    try {
      localStorage.removeItem(CHAT_CACHE_KEY)
    } catch {
      /* ignore */
    }
    api.delete('/chat/history').catch(() => {
      /* best effort */
    })
  }

  const handleCloseTab = (tabId: string) => {
    if (tabs.length <= 1) return
    setTabs(prev => {
      const remaining = prev.filter(t => t.id !== tabId)
      if (activeTabId === tabId) {
        const closedIndex = prev.findIndex(t => t.id === tabId)
        const newActive = remaining[Math.min(closedIndex, remaining.length - 1)]
        setActiveTabId(newActive.id)
      }
      return remaining
    })
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
  }

  const handleSwitchTab = (tabId: string) => {
    if (tabId === activeTabId) return
    setActiveTabId(tabId)
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
    setPendingImage(null)
  }

  const handleReply = (messageId: string) => {
    setReplyingTo(messageId)
    setShowGiphy(false)
  }

  const toggleReaction = (messageId: string, emoji: string) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== messageId) return m
      const reactions = { ...(m.reactions ?? {}) }
      if (reactions[emoji]) {
        reactions[emoji] -= 1
        if (reactions[emoji] <= 0) delete reactions[emoji]
      } else {
        reactions[emoji] = 1
      }
      return { ...m, reactions: Object.keys(reactions).length > 0 ? reactions : undefined }
    }))
  }

  const scrollToMessage = (messageId: string) => {
    const el = document.getElementById(`msg-${messageId}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('ring-2', 'ring-blue-500/50')
      setTimeout(() => el.classList.remove('ring-2', 'ring-blue-500/50'), 1500)
    }
  }

  const renderText = (text: string) => renderTextWithMarkdown(text, MODEL_COLORS)

  // Build the live multi-AI status pill copy. Capitalizes the model
  // name and reads round counts from the latest status payload (with
  // the round and total carried forward from the starting event so
  // every thinking pill can show "round X of Y"). Returns null when
  // there is nothing to show, which hides the pill entirely.
  const formatMultiAiPill = (): string | null => {
    if (!multiAiStatus) return null
    const cap = (s?: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '')
    if (multiAiStatus.phase === 'starting') {
      const names = (multiAiStatus.models ?? []).map(cap)
      const joined = names.length === 2
        ? `${names[0]} and ${names[1]}`
        : names.join(', ')
      const rounds = multiAiStatus.rounds ?? 0
      const roundWord = rounds === 1 ? 'round' : 'rounds'
      return `Starting conversation between ${joined} (${rounds} ${roundWord})`
    }
    if (multiAiStatus.phase === 'thinking') {
      const name = cap(multiAiStatus.model)
      const round = multiAiStatus.round ?? 1
      const total = multiAiStatus.rounds ?? round
      return `${name} is thinking (round ${round} of ${total})`
    }
    if (multiAiStatus.phase === 'speaking') {
      const name = cap(multiAiStatus.model)
      const round = multiAiStatus.round ?? 1
      const total = multiAiStatus.rounds ?? round
      return `${name} is speaking (round ${round} of ${total})`
    }
    return null
  }
  const multiAiPillText = formatMultiAiPill()

  return (
    <div
      data-tour="chat"
      className="fixed top-0 right-0 h-screen bg-slate-950 border-l border-slate-800 z-50 flex flex-col"
      style={{ width: chatWidth }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={handleMouseDown}
        className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-blue-500/30 transition-colors z-10"
      />

      <div className="flex items-center justify-between p-4 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Icon name="chat" className="text-blue-400" />
          <span className="font-bold text-white">ToriChat</span>
          {isConnected && (
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setToolsEnabled(!toolsEnabled)}
            className={`p-1 transition-colors ${toolsEnabled ? 'text-amber-400 hover:text-amber-300' : 'text-slate-500 hover:text-slate-400'}`}
            title={toolsEnabled ? 'Agent mode: ON (can use tools)' : 'Agent mode: OFF (text only)'}
          >
            <Icon name="build" />
          </button>
          <button
            onClick={handleClearHistory}
            className="p-1 text-slate-500 hover:text-red-400 transition-colors"
            title="Clear all chat history"
            data-testid="clear-history-button"
          >
            <Icon name="delete_sweep" />
          </button>
          <button onClick={toggleChat} className="p-1 text-slate-400 hover:text-white transition-colors">
            <Icon name="close" />
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-0.5 px-2 pt-1 pb-0 border-b border-slate-800 overflow-x-auto" data-testid="tab-bar">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => handleSwitchTab(tab.id)}
            className={`group/tab flex items-center gap-1 px-3 py-1.5 text-xs rounded-t-lg transition-colors max-w-[160px] ${
              tab.id === activeTabId
                ? 'bg-slate-900 text-white border-t border-x border-slate-700'
                : 'text-slate-500 hover:text-slate-300 hover:bg-slate-900/50'
            }`}
            title={tab.name}
            data-testid={`tab-${tab.id}`}
          >
            <span className="truncate">{tab.name}</span>
            {tabs.length > 1 && (
              <span
                role="button"
                onClick={(e) => { e.stopPropagation(); handleCloseTab(tab.id) }}
                className="ml-0.5 p-0.5 rounded hover:bg-slate-700 opacity-0 group-hover/tab:opacity-100 transition-opacity"
                data-testid={`close-tab-${tab.id}`}
                title="Close tab"
              >
                <Icon name="close" className="text-[10px]" />
              </span>
            )}
          </button>
        ))}
        <button
          onClick={handleNewConversation}
          className="p-1 text-slate-500 hover:text-white transition-colors ml-0.5"
          title="New conversation"
          data-testid="new-tab-button"
        >
          <Icon name="add" className="text-sm" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-10 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center py-8">
            <Icon name="chat" className="text-4xl text-slate-700 mb-2" />
            <p className="text-slate-500 text-sm">
              Messages go to <span className={MODEL_COLORS[defaultChatModel] ?? 'text-blue-400'}>{defaultChatModel}</span> by default.
            </p>
            <p className="text-slate-600 text-xs mt-1">Use @gemini to talk to a different model. Change your default in Settings.</p>
            <p className="text-slate-600 text-xs mt-1">Type <span className="text-blue-400 font-mono">/giphy</span> to search for GIFs.</p>
            {toolsEnabled && (
              <p className="text-amber-500/60 text-xs mt-2">
                <Icon name="build" className="text-xs align-middle" /> Agent mode is on. Claude can read files, run commands, and more.
              </p>
            )}
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={msg.id} id={`msg-${msg.id}`} className={`group transition-all rounded-xl ${msg.role === 'user' ? 'flex flex-col items-end' : ''}`}>
            {/* Reply context */}
            {msg.replyTo && (
              <ReplyPreview
                message={findMessage(msg.replyTo)}
                onClick={() => scrollToMessage(msg.replyTo!)}
              />
            )}

            {msg.role === 'assistant' && (
              <div className="flex items-center gap-1.5 mb-1">
                {msg.model && (
                  <span className={`text-[10px] font-bold uppercase ${MODEL_COLORS[msg.model] ?? 'text-slate-500'}`}>
                    {msg.model}
                  </span>
                )}
                {!msg.model && (
                  <span className="text-[10px] text-slate-500 font-bold uppercase">Assistant</span>
                )}
                {/* Auto-matched template badge. Shows on the latest assistant
                    message so the user can see which helper kicked in. */}
                {i === messages.length - 1 && activeTemplate && (
                  <span
                    data-testid="template-badge"
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/30 text-[10px] text-blue-300"
                    title={activeTemplate.description || `Using helper: ${activeTemplate.name}`}
                  >
                    Using: {activeTemplate.name}
                    <button
                      onClick={() => setActiveTemplate(null)}
                      className="ml-0.5 text-blue-400 hover:text-blue-200"
                      aria-label="Dismiss helper badge"
                    >
                      <Icon name="close" className="text-[10px]" />
                    </button>
                  </span>
                )}
              </div>
            )}

            <div className={`relative ${msg.role === 'user' ? 'ml-auto max-w-[75%] w-fit' : 'max-w-[85%] w-fit'}`}>
              <div
                className={
                  msg.role === 'user'
                    ? 'inline-block bg-blue-500/20 text-blue-100 px-4 py-2.5 rounded-2xl rounded-br-sm text-sm'
                    : `inline-block border px-4 py-3 rounded-xl text-sm text-slate-300 whitespace-pre-line overflow-hidden break-words ${
                        msg.model ? MODEL_BG[msg.model] ?? 'bg-slate-900 border-slate-800' : 'bg-slate-900 border-slate-800'
                      }`
                }
              >
                {/* GIF display */}
                {msg.gifUrl && (
                  <img
                    src={msg.gifUrl}
                    alt="GIF"
                    className="rounded-lg max-w-full max-h-[300px] object-contain"
                    loading="lazy"
                  />
                )}

                {/* Pasted image display */}
                {msg.imageUrl && (
                  <img
                    src={msg.imageUrl}
                    alt="Image"
                    className="rounded-lg max-w-full max-h-[400px] object-contain mb-1"
                    loading="lazy"
                  />
                )}

                {msg.role === 'user' ? (
                  msg.content ? renderText(msg.content) : null
                ) : (
                  <>
                    {msg.toolCalls && msg.toolCalls.length > 0 && (
                      <div className="mb-2">
                        {msg.toolCalls.map((tc) => (
                          <ToolCallBlock key={tc.id} call={tc} />
                        ))}
                      </div>
                    )}
                    {msg.content && (
                      <CollapsibleText text={msg.content} isLast={i === messages.length - 1} streaming={isStreaming} />
                    )}
                    {/* Show thinking dots any time an assistant bubble is
                        empty and it is the most recent message. This
                        covers the brief window between the empty bubble
                        being created and isStreaming flipping true.
                        Suppressed during multi-AI exchanges because the
                        multi_ai_status pill above already shows thinking
                        and speaking state. Otherwise the user sees two
                        thinking indicators stacked on top of each other,
                        one in the bubble and one in the pill. */}
                    {!msg.content && i === messages.length - 1 && !msg.toolCalls?.length && !multiAiStatus && (
                      <ThinkingDots />
                    )}
                    {isStreaming && i === messages.length - 1 && (msg.toolCalls?.some(tc => tc.result === undefined)) && (
                      <ThinkingDots />
                    )}
                  </>
                )}
              </div>

              {/* Reply and reaction buttons on hover - below the message */}
              <div className={`${msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'} opacity-0 group-hover:opacity-100 mt-1 transition-all`}>
                <div className="flex items-center gap-0.5 z-10">
                  <button
                    onClick={() => handleReply(msg.id)}
                    className="p-1 text-slate-600 hover:text-blue-400 transition-colors"
                    title="Reply"
                  >
                    <Icon name="reply" className="text-sm" />
                  </button>
                  {/* Reaction picker */}
                  <div className="flex items-center gap-0.5 bg-slate-800 border border-slate-700 rounded-lg p-0.5" data-testid={`reaction-bar-${msg.id}`}>
                    {REACTION_EMOJIS.map(emoji => (
                      <button
                        key={emoji}
                        onClick={() => toggleReaction(msg.id, emoji)}
                        className="w-6 h-6 flex items-center justify-center rounded hover:bg-slate-700 transition-colors text-sm"
                        title={`React with ${emoji}`}
                      >
                        {emoji}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Reaction pills */}
              {msg.reactions && Object.keys(msg.reactions).length > 0 && (
                <div className={`flex flex-wrap gap-1 mt-1 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {Object.entries(msg.reactions).map(([emoji, count]) => (
                    <button
                      key={emoji}
                      onClick={() => toggleReaction(msg.id, emoji)}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 hover:border-blue-500/50 text-xs transition-colors"
                      title={`${emoji} ${count}`}
                    >
                      <span>{emoji}</span>
                      <span className="text-slate-400">{count}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {/* Multi-AI live status pill. Renders just below the latest
            assistant bubble so the user can watch the conversation
            move from one model to the other. Hidden whenever no
            multi-AI exchange is in flight. */}
        {multiAiPillText && (
          <div
            data-testid="multi-ai-status-pill"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/30 text-xs text-blue-300"
          >
            <span className="inline-flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </span>
            <span>{multiAiPillText}</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Giphy picker */}
      {showGiphy && (
        <GiphyPicker
          initialSearch={giphyInitialSearch}
          onSelect={sendGif}
          onClose={() => setShowGiphy(false)}
        />
      )}

      <div className="p-3">
        {/* Reply preview bar */}
        {replyingTo && (
          <div className="flex items-center gap-2 mb-2 px-2">
            <Icon name="reply" className="text-blue-400 text-sm" />
            <div className="flex-1 min-w-0">
              <ReplyPreview message={findMessage(replyingTo)} />
            </div>
            <button
              onClick={() => setReplyingTo(null)}
              className="p-0.5 text-slate-500 hover:text-white transition-colors"
            >
              <Icon name="close" className="text-sm" />
            </button>
          </div>
        )}

        {/* Pasted image preview */}
        {pendingImage && (
          <div className="mb-2 relative inline-block">
            <img src={pendingImage} alt="Pasted" className="max-h-32 rounded-lg border border-slate-700" />
            <button
              onClick={() => setPendingImage(null)}
              className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-slate-700 hover:bg-red-500 rounded-full flex items-center justify-center transition-colors"
            >
              <Icon name="close" className="text-xs text-white" />
            </button>
          </div>
        )}

        {/* NEEDLE: removed gray box container around input */}
        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleInputKeyDown}
            onPaste={handlePaste}
            className="flex-1 bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-sm text-slate-300 outline-none focus:ring-2 focus:ring-blue-500/50"
            placeholder={replyingTo ? 'Type your reply...' : `Message ${defaultChatModel}... (/giphy to search GIFs)`}
          />
          <button
            onClick={() => { setShowGiphy(!showGiphy); setGiphyInitialSearch('') }}
            className={`p-2 transition-colors rounded-lg ${showGiphy ? 'text-blue-400 bg-blue-500/10' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'}`}
            title="Search GIFs"
          >
            <Icon name="gif_box" className="text-lg" />
          </button>
          <button
            onClick={handleSend}
            disabled={!input.trim() && !pendingImage}
            className="p-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isStreaming ? (
              <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Icon name="send" className="text-lg" />
            )}
          </button>
        </div>
        {/* Tiny indicator showing which pathway is powering the response.
            Uses plain language so a non-engineer sees at a glance whether
            the chat is using the Claude subscription or the Anthropic key. */}
        {activeBackend && (
          <div
            data-testid="chat-backend-indicator"
            className="mt-1.5 text-[11px] text-slate-500"
          >
            {activeBackend.label}
          </div>
        )}
      </div>
    </div>
  )
}
