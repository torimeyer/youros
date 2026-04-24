import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import Icon from './Icon'
import ConfirmModal from './ConfirmModal'
import { useConfirm } from '../hooks/useConfirm'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'
import { useWebSocket } from '../hooks/useWebSocket'
import { renderMarkdown, renderTextWithMarkdown } from '../lib/markdown'
import { api } from '../lib/api'
import { bumpAgents, bumpCalendar } from '../lib/sidebarBus'
import {
  isRoadmapToTasksRequest,
  type RoadmapToTasksResponse,
} from '../lib/roadmapChatCommand'

// Local cache key. The server is the source of truth for chat history.
// We still mirror to localStorage so the very first paint after a hard
// refresh shows messages instantly while the server fetch is in flight.
const CHAT_CACHE_KEY = 'myos-chat-messages'

// localStorage key for the id of the most recent feature-live notification
// the All pill has already pulsed for. Prevents the pulse from re-firing
// on every ChatPanel mount after the feature has already landed.
const ALL_PILL_PULSE_KEY = 'myos-ephemeral-all-pill-pulsed-for'

// How long the All pill pulses + shows the "New" badge after a Build-it
// landing. 5 seconds matches the auto-dismiss window of the toast.
const ALL_PILL_PULSE_MS = 5000

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
  list_directory: 'Browse folder',
  search_files: 'Search files',
  list_tasks: 'List tasks',
  create_task: 'Create task',
  close_task: 'Close task',
  check_agents: 'Check agents',
  spawn_agent: 'Spawn agent',
  web_search: 'Web search',
  web_fetch: 'Read web page',
  git_status: 'Check for changes',
  git_diff: 'Compare changes',
  git_commit: 'Save changes',
}

const REACTION_EMOJIS = ['👍', '❤️', '😂', '😮', '🔥', '👎']

interface ToolCall {
  id: string
  tool: string
  input: Record<string, unknown>
  /** Raw partial JSON accumulated from streaming input_json_delta events.
   *  The backend forwards each Anthropic input_json_delta fragment as a
   *  tool_use_delta websocket message. We append the fragment here and
   *  try to parse it into `input` once the JSON becomes well-formed.
   *  Never appended to the bubble's text body; kept solely inside the
   *  collapsed tool pill so the chat never shows raw JSON by default. */
  inputJsonRaw?: string
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
  /** thread_id is the id of the root message that started this thread.
   *  null / undefined means the message lives in the root conversation. */
  thread_id?: string | null
  gifUrl?: string
  imageUrl?: string
  reactions?: Record<string, number>
  /** True when this bubble is rendering a WebSocket-level error. The UI
   *  shows an inline Retry button that re-sends the last user turn so
   *  the user never has to re-type their question after a mid-turn
   *  socket drop. */
  isError?: boolean
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

/** Resolve a tool call's best-known input object.
 *  Prefers the structured `input` field; falls back to parsing
 *  `inputJsonRaw` once the streamed JSON becomes well-formed. Keeps the
 *  collapsed pill's one-line summary populated while input_json_delta
 *  fragments are still arriving, without ever rendering the raw JSON
 *  fragments in the assistant bubble body. */
function resolveToolInput(call: ToolCall): Record<string, unknown> {
  if (call.input && Object.keys(call.input).length > 0) return call.input
  if (call.inputJsonRaw) {
    try {
      const parsed = JSON.parse(call.inputJsonRaw)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      // Still mid-stream; JSON isn't valid yet. Fall through.
    }
  }
  return call.input ?? {}
}

function ToolCallBlock({ call }: { call: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const label = call.isMcp
    ? `${call.mcpServer}: ${call.tool}`
    : (TOOL_LABELS[call.tool] ?? call.tool)

  const resolvedInput = resolveToolInput(call)
  let summary = ''
  if (resolvedInput.path) summary = String(resolvedInput.path)
  else if (resolvedInput.command) summary = String(resolvedInput.command)
  else if (resolvedInput.pattern) summary = String(resolvedInput.pattern)
  else if (resolvedInput.title) summary = String(resolvedInput.title)
  else if (resolvedInput.task_id) summary = String(resolvedInput.task_id)

  const labelColor = call.isMcp ? 'text-purple-400' : 'text-amber-400'

  // Pretty-print the assembled arguments for the expanded view so power
  // users can inspect what the tool was called with. Prefer the parsed
  // structured input; fall back to the raw streamed JSON if parse failed
  // (e.g. the stream was truncated before the closing brace). Never shown
  // in the bubble body, only in the collapsed-by-default pill.
  let prettyArgs = ''
  if (Object.keys(resolvedInput).length > 0) {
    try {
      prettyArgs = JSON.stringify(resolvedInput, null, 2)
    } catch {
      prettyArgs = call.inputJsonRaw ?? ''
    }
  } else if (call.inputJsonRaw) {
    prettyArgs = call.inputJsonRaw
  }

  return (
    <div className="my-1.5 border border-slate-700 rounded-lg overflow-hidden bg-slate-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-300 hover:bg-slate-800/50 transition-colors"
        data-testid="tool-call-pill"
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
      {expanded && (
        <div className="border-t border-slate-700 px-3 py-2 max-h-64 overflow-y-auto">
          {prettyArgs && (
            <div className="mb-2" data-testid="tool-call-args">
              <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">Arguments</div>
              <pre className="text-[11px] text-slate-300 whitespace-pre-wrap font-mono leading-relaxed">
                {prettyArgs}
              </pre>
            </div>
          )}
          {call.result !== undefined && (
            <div data-testid="tool-call-result">
              <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">Result</div>
              <pre className="text-[11px] text-slate-400 whitespace-pre-wrap font-mono leading-relaxed">
                {call.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ThinkingDots() {
  return (
    <span
      data-testid="thinking-dots"
      className="inline-flex items-center gap-2 py-1 text-slate-400 text-xs"
    >
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
  const isActivelyStreaming = streaming && isLast

  // Track whether this bubble was JUST streaming on the previous render.
  // The streaming-to-done handoff is when the bubble swaps from plain text
  // to parsed markdown. Even though both state updates commit together,
  // the DOM mutation from a <div whitespace-pre-wrap> holding raw text
  // like "**bold**" to a <div> holding <strong>bold</strong> can briefly
  // paint the plain-text view if the browser begins painting before the
  // full DOM reconciliation lands. We hide that flash by rendering the
  // parsed markdown behind a short opacity transition: the plain-text
  // version fades out while the styled version fades in, so there is no
  // moment where raw markdown is visible.
  const wasStreamingRef = useRef(isActivelyStreaming)
  const [justFinished, setJustFinished] = useState(false)
  useEffect(() => {
    if (wasStreamingRef.current && !isActivelyStreaming) {
      // We just transitioned from streaming to done. Play the fade.
      setJustFinished(true)
      const timer = setTimeout(() => setJustFinished(false), 220)
      wasStreamingRef.current = isActivelyStreaming
      return () => clearTimeout(timer)
    }
    wasStreamingRef.current = isActivelyStreaming
  }, [isActivelyStreaming])

  // While the bubble is still actively streaming, render the raw text with
  // whitespace preserved rather than parsed markdown. Parsing markdown on
  // every new token causes the rendered tree to flicker: a half-written
  // fenced code block collapses into a <pre>, a partial [link](url) shifts
  // layout when the closing ) arrives, and so on. Plain text with
  // whitespace-pre-wrap gives a stable, monotonically growing view. The
  // final markdown render happens once streaming ends.
  //
  // We also deliberately do NOT slice to the last 200 characters here. The
  // old behavior computed `text.slice(-200)` and then trimmed to the next
  // ". " boundary, which meant the visible text would jump backwards every
  // time a new sentence finished inside the sliding window. That looked
  // exactly like "types a sentence then deletes it".
  if (isActivelyStreaming) {
    return (
      <div className="chat-bubble-content whitespace-pre-wrap break-words">
        {text}
      </div>
    )
  }

  // Class applied on the first paint after streaming ends. CSS transitions
  // opacity from 0 to 1 over ~200ms so any paint race at the handoff is
  // hidden behind the fade instead of flashing raw markdown at the user.
  const fadeClass = justFinished ? 'opacity-0 animate-fade-in-fast' : ''

  if (!streaming && isLong && !expanded) {
    // After streaming is done, show first 300 chars with expand option
    return (
      <div>
        <div className={`chat-bubble-content ${fadeClass}`}>{renderMarkdown(text.slice(0, 300))}...</div>
        <button onClick={() => setExpanded(true)} className="text-[10px] text-blue-400 hover:text-blue-300 mt-1">
          Show more
        </button>
      </div>
    )
  }
  return (
    <div className={`chat-bubble-content ${fadeClass}`}>
      <MemoMarkdown text={text} />
      {isLong && expanded && (
        <button onClick={() => setExpanded(false)} className="block text-[10px] text-blue-400 hover:text-blue-300 mt-1">
          Show less
        </button>
      )}
    </div>
  )
}

// Memoize the full markdown render so a settled bubble does not rebuild its
// virtual DOM every time an unrelated piece of ChatPanel state changes. Keyed
// purely on the text content, which is the only input that changes the output.
function MemoMarkdown({ text }: { text: string }) {
  const nodes = useMemo(() => renderMarkdown(text), [text])
  return <>{nodes}</>
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
  const { chatOpen, toggleChat, chatWidth, setChatWidth, isResizing, setIsResizing, defaultChatModel, setDefaultChatModel, sideBySideEnabled, setSideBySideEnabled } = useAppStore()
  const displayOsName = useAppStore((s) => s.displayOsName())

  // One-time "New" pulse on the All pill after a Build-it feature lands.
  // Reads lastFeatureLive from the notifications store (set when the
  // TopBar poll picks up a spec_complete row). Fires for ALL_PILL_PULSE_MS
  // and then settles. Dedup'd in localStorage on the notification id so
  // re-mounting the panel on a later page visit does not re-pulse.
  const lastFeatureLive = useNotificationStore((s) => s.lastFeatureLive)
  const [allPillPulse, setAllPillPulse] = useState(false)
  useEffect(() => {
    if (!lastFeatureLive) return
    let alreadyPulsed: string | null = null
    try {
      alreadyPulsed = window.localStorage.getItem(ALL_PILL_PULSE_KEY)
    } catch {
      // storage unavailable; fall through and pulse anyway so the user
      // still sees the signal.
    }
    if (alreadyPulsed === lastFeatureLive.id) return
    setAllPillPulse(true)
    try {
      window.localStorage.setItem(ALL_PILL_PULSE_KEY, lastFeatureLive.id)
    } catch {
      // ignore
    }
    const t = window.setTimeout(() => setAllPillPulse(false), ALL_PILL_PULSE_MS)
    return () => window.clearTimeout(t)
  }, [lastFeatureLive])

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
  const [isListening, setIsListening] = useState(false)
  const speechRecRef = useRef<SpeechRecognition | null>(null)
  const [speechSupported] = useState(() =>
    typeof window !== 'undefined' &&
    ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)
  )
  const [isStreaming, setIsStreaming] = useState(false)
  // Gate for the "instant thinking dots" indicator. Set synchronously in
  // the send handlers so the ThinkingDots render in the same React commit
  // as the user's bubble. Cleared as soon as the first server event for
  // this turn lands (thinking, token, multi-AI status, turn start, tool
  // use, done, error). Without this, the assistant bubble briefly paints
  // with just the model label and no dots while the socket hop to the
  // server completes.
  const [placeholderAwaitingServer, setPlaceholderAwaitingServer] = useState(false)
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
  // Maps model name ("claude", "gemini") to the bubble id currently
  // receiving that model's tokens. Populated on multi_ai_turn_start and
  // cleared on multi_ai_turn_end. Used by parallel broadcast fan-out:
  // when the backend sends a token frame with a `model` field, we route
  // the text into the bubble that matches, rather than the most recently
  // opened bubble. Without this map, two parallel streams would collide
  // inside a single bubble.
  const bubbleIdByModelRef = useRef<Map<string, string>>(new Map())
  // State-backed mirror of bubbleIdByModelRef so bubble renders can show
  // a thinking indicator for every model currently in flight, not just
  // the most recently opened bubble. Populated on multi_ai_turn_start,
  // cleared on multi_ai_turn_end. Drives the parallel thinking-dots
  // render path for the All pill.
  const [activeStreamingBubbleIds, setActiveStreamingBubbleIds] = useState<Set<string>>(new Set())
  const [replyingTo, setReplyingTo] = useState<string | null>(null)
  // Set of thread root ids that are currently collapsed.
  const [collapsedThreads, setCollapsedThreads] = useState<Set<string>>(new Set())
  const toggleThread = useCallback((threadRootId: string) => {
    setCollapsedThreads(prev => {
      const next = new Set(prev)
      if (next.has(threadRootId)) {
        next.delete(threadRootId)
      } else {
        next.add(threadRootId)
      }
      return next
    })
  }, [])
  const [showGiphy, setShowGiphy] = useState(false)
  const [giphyInitialSearch, setGiphyInitialSearch] = useState('')
  const [pendingImage, setPendingImage] = useState<string | null>(null)
  const [commandHistory, setCommandHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const lastEscRef = useRef(0)
  const lastUpRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // Grace-window state for the "Done." fallback.
  // When `done` arrives we do not immediately show "Done." because some
  // providers (notably Gemini) send a `done` event before the last text
  // tokens have been flushed through the WebSocket. Instead we start a
  // 500ms timer per message. If a token lands within that window the
  // timer is cancelled and "Done." never flashes. Only after the timer
  // fires with no new tokens do we mark the message as confirmed-final.
  // confirmedDoneIds drives the fallback render so the bubble is never
  // blank on a genuine tool-only turn, but also never shows "Done."
  // while Gemini's text is still on its way.
  const doneGraceTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const [confirmedDoneIds, setConfirmedDoneIds] = useState<Set<string>>(new Set())
  // Track which bubble IDs received at least one text token. When the
  // grace window fires and 0 tokens were received, we know the model
  // produced no content and should show a retry prompt, not "Done.".
  const bubbleGotTokensRef = useRef<Set<string>>(new Set())
  // Track whether we have received any real server event (token, thinking,
  // tool_use, multi_ai_status, etc.) for the current turn. This prevents
  // the dead-backend timer from firing after the server has started
  // responding but before text tokens have landed.
  const receivedAnyServerEventRef = useRef(false)
  // Deduplicate lastMessage processing. The useEffect depends on
  // [lastMessage, currentModel]. When sendMessage sets currentModel the
  // effect re-runs with the stale lastMessage from the previous turn,
  // causing a phantom `done` to fire against the new turn's assistant
  // bubble. By tracking the last-processed reference we skip the
  // duplicate. This ref is NOT reset when a new turn starts because
  // the new turn's first real event will be a different object reference.
  const processedMessageRef = useRef<unknown>(null)
  // Tracked background agents spawned from chat via the spawn_agent
  // tool. Each entry is polled every ~30s; on a terminal transition
  // we append a plain-language bubble and drop the entry so the
  // effect stops polling. Without this the chat goes silent after the
  // "Spawn agent" card until the user asks manually, which was the
  // feedback regression Tori saw on optimize-inline-chat-speed.
  type TrackedAgent = { name: string }
  const [trackedAgents, setTrackedAgents] = useState<TrackedAgent[]>([])

  const { connect, disconnect, send, lastMessage, isConnected } = useWebSocket('/ws/chat')
  const { confirm, confirmProps } = useConfirm()

  // Clear all pending Done. grace timers and reset confirmed state.
  // Called when switching tabs, clearing history, or closing a tab so
  // timers from a previous conversation do not fire in the new context.
  const clearAllDoneGraceTimers = useCallback(() => {
    doneGraceTimersRef.current.forEach(timer => clearTimeout(timer))
    doneGraceTimersRef.current.clear()
    setConfirmedDoneIds(new Set())
    bubbleGotTokensRef.current.clear()
  }, [])

  const findMessage = useCallback((id: string) => messages.find(m => m.id === id), [messages])

  // Connect when the chat panel opens; disconnect when it closes.
  // Do NOT depend on isConnected here: that would re-trigger connect()
  // on every failed attempt and create an immediate-retry storm.
  // The hook's own exponential backoff handles reconnects after errors.
  useEffect(() => {
    if (chatOpen) {
      connect()
    } else {
      disconnect()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen])

  useEffect(() => {
    if (!lastMessage) return

    // Deduplicate: the effect fires when currentModel changes but
    // lastMessage has not changed (same object reference). Processing
    // the same event twice causes a stale `done` from the previous turn
    // to start a grace timer against the new turn's assistant bubble,
    // producing a premature "No response received" error while Claude's
    // first token is still ~5s away.
    if (lastMessage === processedMessageRef.current) return
    processedMessageRef.current = lastMessage

    // The server has spoken for this turn. Drop the instant-dots gate so
    // the render falls back to the normal isStreaming driven indicator.
    // Any event type counts, including `done` and `error`, so the flag
    // never outlives its own turn even if the stream is empty.
    setPlaceholderAwaitingServer(false)

    // Track real server events (anything except model_label, template_matched,
    // backend_active, and done which are metadata/lifecycle). This lets
    // the dead-backend timer distinguish "server never started responding"
    // from "server responded but produced zero text tokens".
    const isRealServerEvent = !['model_label', 'template_matched', 'backend_active', 'done'].includes(lastMessage.type as string)
    if (isRealServerEvent) {
      receivedAnyServerEventRef.current = true
    }

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
      // Model-tagged tokens (sent by parallel broadcast) must route to
      // the bubble for that specific model, not the most recently
      // opened bubble. Without this, two parallel streams collide
      // inside one bubble and the other bubble stays blank.
      const tokenModel = (lastMessage as unknown as { model?: string }).model
      const routedBubbleId =
        (tokenModel && bubbleIdByModelRef.current.get(tokenModel)) ||
        currentBubbleIdRef.current
      setMessages(prev => {
        // When a multi-AI turn is active, route the token into the
        // bubble for the speaker so the tokens never bleed into a
        // previous bubble. Otherwise fall back to the legacy
        // append-to-last-assistant path the single-model flow uses.
        const bubbleId = routedBubbleId
        if (bubbleId) {
          // Cancel any pending Done. grace timer for this bubble.
          const existing = doneGraceTimersRef.current.get(bubbleId)
          if (existing !== undefined) {
            clearTimeout(existing)
            doneGraceTimersRef.current.delete(bubbleId)
          }
          // Track that this bubble received at least one text token.
          bubbleGotTokensRef.current.add(bubbleId)
          return prev.map(m => {
            if (m.id === bubbleId && m.role === 'assistant') {
              const baseContent = m.isError ? '' : m.content
              return { ...m, content: baseContent + (lastMessage.data as string), isError: undefined }
            }
            return m
          })
        }
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          // Cancel any pending Done. grace timer for the last bubble.
          const existingTimer = doneGraceTimersRef.current.get(last.id)
          if (existingTimer !== undefined) {
            clearTimeout(existingTimer)
            doneGraceTimersRef.current.delete(last.id)
          }
          // Track that this bubble received at least one text token.
          bubbleGotTokensRef.current.add(last.id)
          // If the bubble was converted to a zero-token error by the
          // grace timer (Bug A fix), a late arriving token means the
          // model DID produce content. Clear the error state and reset
          // content so the real text replaces the error message.
          const baseContent = last.isError ? '' : last.content
          updated[updated.length - 1] = {
            ...last,
            content: baseContent + lastMessage.data,
            isError: undefined,
          }
        }
        return updated
      })
      // If a token arrives for a message already in confirmedDoneIds (rare
      // race, e.g. Gemini sends done then flushes tokens >500ms later),
      // remove it so the "Done." label disappears and the text shows instead.
      setConfirmedDoneIds(prev => {
        if (prev.size === 0) return prev
        const bubbleId = currentBubbleIdRef.current
        if (bubbleId) {
          // Multi-AI path: remove the specific bubble.
          if (prev.has(bubbleId)) {
            const next = new Set(prev)
            next.delete(bubbleId)
            return next
          }
          return prev
        }
        // Non-multi-AI path: any token landing means the last assistant bubble
        // is still receiving content. Clear all confirmed-done ids so the
        // "Done." label disappears and the arriving text renders instead.
        return new Set<string>()
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
      // reuse it for the FIRST turn only so the panel does not show a
      // dangling empty bubble above the conversation.
      //
      // The backend fires a turn_start for every model UP FRONT in the
      // parallel broadcast (All pill) case, so we may get two of these
      // back to back before any tokens arrive. Each one gets its own
      // bubble, and the model-to-bubble map lets token/error frames tag
      // themselves with a `model` field to land in the right bubble
      // even while the sibling model is still streaming. The reuse is
      // gated on bubbleIdByModelRef being empty: once we have mapped
      // any model to a bubble in this burst, every subsequent turn_start
      // must push a NEW bubble. Without this gate the second turn_start
      // would reuse the first model's empty bubble (both model labels
      // get mapped to the same bubble id) and the parallel fan-out
      // collapses to a single bubble that both streams dump into.
      const data = lastMessage.data as unknown as { model: string; round: number }
      if (!data?.model) return
      // Optimistic-placeholder reconciliation: in broadcast mode
      // sendMessage pre-creates one bubble per model so thinking dots
      // render side by side from the first commit. If that placeholder
      // already exists, reuse it instead of pushing a new bubble. This
      // keeps the two optimistic bubbles stable while tokens arrive.
      const existingBubbleId = bubbleIdByModelRef.current.get(data.model)
      if (existingBubbleId) {
        currentBubbleIdRef.current = existingBubbleId
        setActiveStreamingBubbleIds(prev => {
          if (prev.has(existingBubbleId)) return prev
          const next = new Set(prev)
          next.add(existingBubbleId)
          return next
        })
        setIsStreaming(true)
        return
      }
      const newBubbleId = genId()
      const canReusePlaceholder = bubbleIdByModelRef.current.size === 0
      setMessages(prev => {
        const last = prev[prev.length - 1]
        if (
          canReusePlaceholder &&
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
      bubbleIdByModelRef.current.set(data.model, newBubbleId)
      setActiveStreamingBubbleIds(prev => {
        const next = new Set(prev)
        next.add(newBubbleId)
        return next
      })
      setIsStreaming(true)
    } else if (lastMessage.type === 'multi_ai_turn_end') {
      // Close the current bubble. The bubble stays in the list, just
      // no longer accepts streaming tokens. A late stray token after
      // turn_end is dropped, which is the safe behavior.
      const endData = lastMessage.data as unknown as { model?: string }
      if (endData?.model) {
        const endedBubbleId = bubbleIdByModelRef.current.get(endData.model)
        bubbleIdByModelRef.current.delete(endData.model)
        if (endedBubbleId) {
          setActiveStreamingBubbleIds(prev => {
            if (!prev.has(endedBubbleId)) return prev
            const next = new Set(prev)
            next.delete(endedBubbleId)
            return next
          })
        }
      }
      currentBubbleIdRef.current = null
    } else if (lastMessage.type === 'tool_use') {
      const data = lastMessage.data as unknown as { tool: string; input: Record<string, unknown>; id: string }
      // Route tool_use frames by the top-level model field when the
      // backend stamps one (broadcast path). Without this the Claude
      // tool call lands in whichever bubble happens to be last in the
      // message list, which is Gemini's bubble whenever Gemini's
      // turn_start fired second. Falls back to the last assistant
      // bubble for the single-model path where no model tag is sent.
      const toolModel = (lastMessage as unknown as { model?: string }).model
      const routedBubbleId = toolModel ? bubbleIdByModelRef.current.get(toolModel) : undefined
      setMessages(prev => {
        const updated = [...prev]
        let idx = updated.length - 1
        if (routedBubbleId) {
          const found = updated.findIndex(m => m.id === routedBubbleId)
          if (found !== -1) idx = found
        }
        const target = updated[idx]
        if (target && target.role === 'assistant') {
          const calls = [...(target.toolCalls ?? [])]
          calls.push({ id: data.id, tool: data.tool, input: data.input })
          updated[idx] = { ...target, toolCalls: calls }
        }
        return updated
      })
      // When the chat assistant spawns a background agent (e.g. "spawn
      // roadmap"), bump the agents bus so the Agents page, sidebar
      // badge, and any other listening surface refetch right away. The
      // chat WebSocket does not go through the api wrapper so the
      // automatic bump in api.ts never fires for chat-driven spawns.
      if (data.tool === 'spawn_agent') {
        bumpAgents()
        // Track this agent so the panel polls /status-feedback and
        // drops a plain-language follow-up bubble when the agent
        // transitions to a terminal state (completed, failed,
        // cancelled, stale). Without this the chat goes silent after
        // the Spawn agent card and the user has to ask manually.
        const spawnInput = data.input as { name?: unknown } | undefined
        const agentName =
          typeof spawnInput?.name === 'string' ? spawnInput.name.trim() : ''
        if (agentName) {
          setTrackedAgents(prev => {
            if (prev.some(a => a.name === agentName)) return prev
            return [...prev, { name: agentName }]
          })
        }
      }
    } else if (lastMessage.type === 'mcp_tool_use') {
      const data = lastMessage.data as unknown as { tool: string; server: string; input: Record<string, unknown>; id: string }
      const toolModel = (lastMessage as unknown as { model?: string }).model
      const routedBubbleId = toolModel ? bubbleIdByModelRef.current.get(toolModel) : undefined
      setMessages(prev => {
        const updated = [...prev]
        let idx = updated.length - 1
        if (routedBubbleId) {
          const found = updated.findIndex(m => m.id === routedBubbleId)
          if (found !== -1) idx = found
        }
        const target = updated[idx]
        if (target && target.role === 'assistant') {
          const calls = [...(target.toolCalls ?? [])]
          calls.push({ id: data.id, tool: data.tool, input: data.input, isMcp: true, mcpServer: data.server })
          updated[idx] = { ...target, toolCalls: calls }
        }
        return updated
      })
    } else if (lastMessage.type === 'tool_use_delta') {
      // Streaming fragment of a tool_use block's input JSON. Anthropic
      // sends these as `input_json_delta` events; the backend forwards
      // each fragment keyed by the owning tool_use id. We append the
      // partial JSON to the matching tool call's inputJsonRaw buffer
      // and, when the buffer becomes well-formed JSON, promote it to
      // the structured `input` object so the pill's one-line summary
      // (path / command / pattern / title / task_id) populates while
      // streaming. CRITICAL: this branch never touches the bubble's
      // `content` field. Raw JSON fragments must never leak into the
      // visible assistant text body.
      const data = lastMessage.data as unknown as {
        id: string
        partial_json?: string
      }
      const fragment = typeof data?.partial_json === 'string' ? data.partial_json : ''
      if (!data?.id || !fragment) return
      setMessages(prev => {
        const ownerIdx = prev.findIndex(
          m => m.role === 'assistant' && m.toolCalls?.some(tc => tc.id === data.id),
        )
        if (ownerIdx === -1) return prev
        const target = prev[ownerIdx]
        if (!target.toolCalls) return prev
        const calls = target.toolCalls.map(tc => {
          if (tc.id !== data.id) return tc
          const nextRaw = (tc.inputJsonRaw ?? '') + fragment
          let nextInput = tc.input
          try {
            const parsed = JSON.parse(nextRaw)
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
              nextInput = parsed as Record<string, unknown>
            }
          } catch {
            // Still mid-stream; keep the prior structured input.
          }
          return { ...tc, inputJsonRaw: nextRaw, input: nextInput }
        })
        const updated = [...prev]
        updated[ownerIdx] = { ...target, toolCalls: calls }
        return updated
      })
    } else if (lastMessage.type === 'tool_result') {
      const data = lastMessage.data as unknown as { id: string; result: string }
      // Find the bubble that owns this tool call by id, regardless of
      // which bubble happens to be last. In broadcast mode the Claude
      // tool result could arrive while Gemini's bubble is still
      // streaming tokens.
      let matchedToolName: string | undefined
      setMessages(prev => {
        const updated = [...prev]
        const ownerIdx = updated.findIndex(
          m => m.role === 'assistant' && m.toolCalls?.some(tc => tc.id === data.id),
        )
        const idx = ownerIdx !== -1 ? ownerIdx : updated.length - 1
        const target = updated[idx]
        if (target && target.role === 'assistant' && target.toolCalls) {
          const calls = target.toolCalls.map(tc => {
            if (tc.id === data.id) {
              matchedToolName = tc.tool
              return { ...tc, result: data.result }
            }
            return tc
          })
          updated[idx] = { ...target, toolCalls: calls }
        }
        return updated
      })
      // When the chat assistant creates a calendar event, tell the
      // Calendar page to refetch immediately. The backend tool handler
      // writes directly through services.calendar.create_event so the
      // normal "bump on POST /calendar" path in api.ts never fires for
      // chat-driven creates. Without this the Calendar tab stays stale
      // until the user reloads. Only bump on apparent success. The tool
      // returns a string starting with "Created calendar event" on
      // success and "Could not..." or "Google Calendar is not connected"
      // on failure, which we skip.
      if (
        matchedToolName === 'create_calendar_event' &&
        typeof data.result === 'string' &&
        data.result.startsWith('Created calendar event')
      ) {
        bumpCalendar()
      }
    } else if (lastMessage.type === 'mcp_tool_result') {
      const data = lastMessage.data as unknown as { id: string; result: string; is_error: boolean }
      setMessages(prev => {
        const updated = [...prev]
        const ownerIdx = updated.findIndex(
          m => m.role === 'assistant' && m.toolCalls?.some(tc => tc.id === data.id),
        )
        const idx = ownerIdx !== -1 ? ownerIdx : updated.length - 1
        const target = updated[idx]
        if (target && target.role === 'assistant' && target.toolCalls) {
          const calls = target.toolCalls.map(tc =>
            tc.id === data.id ? { ...tc, result: data.is_error ? `Error: ${data.result}` : data.result } : tc,
          )
          updated[idx] = { ...target, toolCalls: calls }
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
      // Resolve lastMsgId from the closure messages snapshot BEFORE
      // calling setMessages. Relying on the setMessages reducer to set
      // lastMsgId is a bug: React 18 defers the reducer so the
      // enclosing code runs with lastMsgId still null, and the
      // grace-window timer below never gets registered.
      const lastExisting = messages[messages.length - 1]
      const lastMsgId: string | null =
        lastExisting && lastExisting.role === 'assistant' ? lastExisting.id : null
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          // Stamp the model name only when the bubble does not already
          // carry one. In parallel broadcast the Claude turn_start and
          // Gemini turn_start already stamp each bubble with its own
          // model via bubbleIdByModelRef, so overwriting here would
          // flip the last bubble's label to currentModel (the dropdown
          // default) and cause the visible relabel bug where Gemini's
          // bubble suddenly renders with a Claude header.
          const withModel = currentModel && !last.model
            ? { ...last, model: currentModel }
            : last
          // Close any tool calls that never received a result event.
          // The claude-code provider handles tools internally and never
          // sends tool_result messages, so result stays undefined
          // forever. Marking them '' on done stops the spinner and lets
          // the check-circle render without waiting for a result that
          // will never arrive.
          const resolvedCalls = withModel.toolCalls?.map(tc =>
            tc.result === undefined ? { ...tc, result: '' } : tc,
          )
          updated[updated.length - 1] = { ...withModel, toolCalls: resolvedCalls }
        }
        return updated
      })
      setIsStreaming(false)
      setCurrentModel(null)
      // Defensive cleanup. The complete phase usually arrives just
      // before done, but if the backend ever drops it the pill must
      // still go away when the stream ends.
      setMultiAiStatus(null)
      currentBubbleIdRef.current = null
      // Clear per-model tracking so stale thinking indicators from a
      // stream that did not emit a proper turn_end (e.g. backend
      // crashed mid-stream) do not leak into the next turn.
      bubbleIdByModelRef.current.clear()
      setActiveStreamingBubbleIds(new Set())
      // Start the 500ms grace window before showing "Done." in the bubble.
      // Some providers (Gemini) can flush a `done` event slightly before
      // their last text tokens reach the client, so we delay the fallback
      // label to avoid a visible flash. If a token arrives inside the
      // window, the token handler cancels the timer (see below).
      if (lastMsgId) {
        const msgId = lastMsgId
        const hasToolCalls = !!(lastExisting && lastExisting.toolCalls?.length)
        const timer = setTimeout(() => {
          doneGraceTimersRef.current.delete(msgId)
          const gotTokens = bubbleGotTokensRef.current.has(msgId)
          if (!gotTokens && !hasToolCalls) {
            // The model produced no text and no tool calls. This is a
            // genuine empty response (the dedup guard already filtered
            // stale `done` events from a previous turn). Show a
            // user-friendly error with a retry button instead of the
            // misleading "Done." label.
            setMessages(prev => {
              const updated = [...prev]
              const target = updated.find(m => m.id === msgId)
              if (target && target.role === 'assistant' && !target.content?.trim()) {
                const idx = updated.indexOf(target)
                updated[idx] = {
                  ...target,
                  content: 'No response received. Please try again.',
                  isError: true,
                }
              }
              return updated
            })
          } else {
            // Normal turn end: either tokens arrived or tool calls happened.
            // Add to confirmedDoneIds so tool-only turns show "Done.".
            setConfirmedDoneIds(prev => {
              const next = new Set(prev)
              next.add(msgId)
              return next
            })
          }
        }, 500)
        doneGraceTimersRef.current.set(msgId, timer)
      }
    } else if (lastMessage.type === 'error') {
      // If the error is tagged with a model, it belongs to just that
      // model's bubble in a parallel broadcast. Don't tear down the
      // whole streaming state, because the sibling model is still
      // running. Only globally reset when the error is ungrouped
      // (single-model flow) or when it's the last outstanding model.
      const errorModel = (lastMessage as unknown as { model?: string }).model
      const targetBubbleId = errorModel
        ? bubbleIdByModelRef.current.get(errorModel)
        : undefined
      const hasOtherModelStreaming =
        errorModel &&
        Array.from(bubbleIdByModelRef.current.keys()).some(m => m !== errorModel)

      if (!hasOtherModelStreaming) {
        setIsStreaming(false)
        setPlaceholderAwaitingServer(false)
        setCurrentModel(null)
        setMultiAiStatus(null)
        currentBubbleIdRef.current = null
      }
      setMessages(prev => {
        const updated = [...prev]
        let idx = updated.length - 1
        if (targetBubbleId) {
          const found = updated.findIndex(m => m.id === targetBubbleId)
          if (found !== -1) idx = found
        }
        const target = updated[idx]
        if (target && target.role === 'assistant') {
          // Flag the bubble so the UI renders an inline Retry button.
          // Re-send logic: the Retry button finds the last user message
          // in this tab and replays its content through sendMessage, so
          // the user never has to re-type after a mid-turn socket drop.
          updated[idx] = {
            ...target,
            content: `Error: ${lastMessage.data}`,
            isError: true,
          }
        }
        return updated
      })
    }
  }, [lastMessage, currentModel])

  // Dead-backend safety net: if the UI has been in the
  // placeholderAwaitingServer state for 30 seconds and no real server
  // event has arrived, show the error. This catches cases where the
  // backend is genuinely unreachable and neither tokens nor a `done`
  // event will ever arrive.
  useEffect(() => {
    if (!placeholderAwaitingServer && !isStreaming) return
    // Only start the dead-backend timer when we are waiting and have
    // NOT yet received any real server event for this turn.
    if (receivedAnyServerEventRef.current) return
    const deadTimer = setTimeout(() => {
      // Re-check: if a real event arrived in the meantime, abort.
      if (receivedAnyServerEventRef.current) return
      setIsStreaming(false)
      setPlaceholderAwaitingServer(false)
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant' && !last.content?.trim()) {
          const idx = updated.indexOf(last)
          // Backend went silent for 30s with no events. This is almost
          // always a dead backend or a WebSocket that dropped without a
          // close frame. The server-side provider timeouts will surface
          // a more specific message when the provider itself stalls.
          updated[idx] = {
            ...last,
            content:
              'The server did not send any response in 30 seconds. ' +
              'The backend may be restarting or unreachable. ' +
              'Please try again.',
            isError: true,
          }
        }
        return updated
      })
    }, 30_000)
    return () => clearTimeout(deadTimer)
  }, [placeholderAwaitingServer, isStreaming])

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

  // Spawn-agent follow-up feedback.
  // Before this effect, the chat would show one "Spawn agent" card when
  // the assistant called spawn_agent and then go silent. Tori had to
  // ask "did it work?" to find out if the agent was still running or
  // had finished. That felt like the agent vanished. Now, every
  // tracked agent (populated from the spawn_agent tool_use handler)
  // gets polled once every 30 seconds. On the first terminal status
  // we append a plain-language assistant bubble (completed / failed /
  // cancelled / stale) and drop the row so the effect does not post
  // again. The bubble uses the server's `feedback`
  // string, which already weaves in the agent's own summary or the
  // specific reason it stopped.
  useEffect(() => {
    if (trackedAgents.length === 0) return
    const pending = trackedAgents
    let cancelled = false
    const pollOnce = async () => {
      for (const agent of pending) {
        try {
          const resp = await api.get<{
            exists: boolean
            terminal: boolean
            status: string | null
            feedback: string | null
          }>(`/agents/${encodeURIComponent(agent.name)}/status-feedback`)
          if (cancelled) return
          if (!resp) continue
          // Unknown name: stop polling so we do not loop forever on a
          // typo or an agent the backend never recorded.
          if (!resp.exists) {
            setTrackedAgents(prev => prev.filter(a => a.name !== agent.name))
            continue
          }
          if (resp.terminal && resp.feedback) {
            setMessages(prev => [
              ...prev,
              {
                id: genId(),
                role: 'assistant',
                content: resp.feedback || `Agent ${agent.name} finished.`,
                model: 'myos',
              },
            ])
            setTrackedAgents(prev => prev.filter(a => a.name !== agent.name))
          }
        } catch {
          // Network/backend down; try again on next tick.
        }
      }
    }
    // Poll immediately on mount so a very short agent (ran in under
    // 30s) still produces a bubble, then every 30s while any agent
    // is still running.
    pollOnce()
    const timer = setInterval(pollOnce, 30_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackedAgents, setMessages])

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

    // Handle "create tasks from this roadmap" and its variants. The
    // backend reads the latest roadmap.md, parses items, and creates
    // tasks. We show the user's message and a reply bubble inline
    // without the WebSocket round-trip so the model cost is zero.
    if (isRoadmapToTasksRequest(text)) {
      handleRoadmapToTasks(text.trim())
      return
    }

    if (!text.trim() && !pendingImage) return
    // Clear any prior template badge so the next response shows its own.
    setActiveTemplate(null)

    // Determine thread_id for this message. If replying to something that
    // already belongs to a thread, inherit that thread_id. If replying to a
    // root message (no thread_id), start a new thread rooted at that message.
    let replyThreadId: string | null = null
    if (replyingTo) {
      const parentMsg = messages.find(m => m.id === replyingTo)
      if (parentMsg) {
        replyThreadId = parentMsg.thread_id ?? parentMsg.id
      }
    }

    const userMsgId = genId()
    const userMessage: Message = {
      id: userMsgId,
      role: 'user',
      content: text.trim(),
      replyTo: replyingTo || undefined,
      thread_id: replyThreadId,
      imageUrl: pendingImage || undefined,
    }

    const mentionMatch = text.match(/@(\w+)/i)
    const detectedModel = mentionMatch ? mentionMatch[1].toLowerCase() : defaultChatModel

    const updatedMessages = [...messages, userMessage]
    const assistantMessage: Message = {
      id: genId(),
      role: 'assistant',
      content: '',
      model: detectedModel,
      thread_id: replyThreadId,
    }
    setMessages([...updatedMessages, assistantMessage])
    setIsStreaming(true)
    setPlaceholderAwaitingServer(true)
    setCurrentModel(detectedModel)
    setReplyingTo(null)
    setPendingImage(null)
    // Reset turn-level tracking for the new message so stale state
    // from the previous turn does not leak into the new one.
    // Note: processedMessageRef is intentionally NOT reset here. The
    // stale lastMessage object from the previous turn will be the same
    // reference, so the dedup guard in the useEffect skips it. The new
    // turn's first real WebSocket event will be a different object.
    receivedAnyServerEventRef.current = false

    // Build the messages payload. Include the model field on assistant
    // messages so the backend can detect multi-model conversations and
    // include the full history for models that need cross-model context.
    const apiMessages = updatedMessages.map(m => {
      const base: Record<string, string> = { role: m.role, content: m.content || '' }
      if (m.model) base.model = m.model
      if (m.imageUrl) { base.content = m.content || '[image]'; (base as any).image = m.imageUrl }
      if (m.gifUrl) base.content = `[gif:${m.gifUrl}]`
      return base
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
      tab_id: activeTabId,
      replyToId: replyingTo || undefined,
      thread_id: replyThreadId || undefined,
      // All pill: when sideBySideEnabled is on, fan out to Claude
      // AND Gemini in parallel on the backend.
      side_by_side: sideBySideEnabled,
    })
  }

  // Intercept the "create tasks from this roadmap" chat command. The
  // user's message is appended as a normal user bubble, the backend
  // parses the latest roadmap and creates tasks, and the reply lands
  // as an assistant bubble with a link to the Tasks page.
  const handleRoadmapToTasks = (text: string) => {
    const userMessage: Message = {
      id: genId(),
      role: 'user',
      content: text,
    }
    const placeholder: Message = {
      id: genId(),
      role: 'assistant',
      content: '',
      model: 'myos',
    }
    setMessages((prev) => [...prev, userMessage, placeholder])
    setReplyingTo(null)

    api
      .post<RoadmapToTasksResponse>('/chat/roadmap/create-tasks', {})
      .then((resp) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === placeholder.id
              ? { ...m, content: resp?.reply || 'Done.' }
              : m,
          ),
        )
      })
      .catch((err) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === placeholder.id
              ? {
                  ...m,
                  content:
                    'I could not create tasks from the roadmap: ' +
                    (err?.message || 'unknown error'),
                  isError: true,
                }
              : m,
          ),
        )
      })
  }

  // Re-send the last user turn after a WebSocket-level error.
  //
  // After a mid-turn socket drop the last bubble carries isError=true
  // and shows a "Retry" button. The retry flow drops the failed
  // assistant bubble and the user bubble that preceded it, then calls
  // sendMessage with that user text. sendMessage creates a fresh
  // assistant placeholder and opens a new stream, so the user never has
  // to re-type their question.
  const retryLastTurn = () => {
    const lastMsg = messages[messages.length - 1]
    if (!lastMsg || lastMsg.role !== 'assistant' || !lastMsg.isError) return
    const userMsg = messages[messages.length - 2]
    if (!userMsg || userMsg.role !== 'user') return
    const text = userMsg.content || ''
    if (!text.trim()) return
    // Drop the failed assistant bubble and the original user bubble so
    // sendMessage does not duplicate them. sendMessage will append a
    // fresh pair using the same text.
    setMessages(prev => prev.slice(0, prev.length - 2))
    sendMessage(text)
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
    setPlaceholderAwaitingServer(true)
    setCurrentModel(defaultChatModel)
    setShowGiphy(false)
    setReplyingTo(null)
    receivedAnyServerEventRef.current = false

    // Send messages with GIF URLs so the AI can see them
    const apiMessages = updatedMessages.map(m => ({
      role: m.role,
      content: m.gifUrl ? `[gif:${m.gifUrl}]` : (m.content || ''),
    }))

    send({
      model: `@${defaultChatModel}`,
      messages: apiMessages,
      tools: toolsEnabled,
      tab_id: activeTabId,
      // All pill: when sideBySideEnabled is on, fan out to Claude
      // AND Gemini in parallel on the backend.
      side_by_side: sideBySideEnabled,
    })
  }

  const toggleSpeech = useCallback(() => {
    if (isListening && speechRecRef.current) {
      speechRecRef.current.stop()
      setIsListening(false)
      return
    }
    const SpeechRec = (window as unknown as Record<string, unknown>).SpeechRecognition || (window as unknown as Record<string, unknown>).webkitSpeechRecognition
    if (!SpeechRec) return
    const recognition = new (SpeechRec as new () => SpeechRecognition)()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.maxAlternatives = 1
    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = event.results[0]?.[0]?.transcript ?? ''
      if (transcript) {
        setInput((prev) => (prev ? prev + ' ' + transcript : transcript))
      }
    }
    recognition.onend = () => {
      setIsListening(false)
      speechRecRef.current = null
    }
    recognition.onerror = () => {
      setIsListening(false)
      speechRecRef.current = null
    }
    speechRecRef.current = recognition
    recognition.start()
    setIsListening(true)
  }, [isListening])

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
    clearAllDoneGraceTimers()
  }

  const handleClearHistory = async () => {
    const ok = await confirm({
      title: 'Clear all chat history?',
      message: 'This cannot be undone.',
      confirmLabel: 'Clear history',
      danger: true,
    })
    if (!ok) return
    const freshTab: ChatTab = { id: genId(), name: 'New Chat', messages: [], updatedAt: new Date().toISOString() }
    setTabs([freshTab])
    setActiveTabId(freshTab.id)
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
    clearAllDoneGraceTimers()
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
    clearAllDoneGraceTimers()
  }

  const handleSwitchTab = (tabId: string) => {
    if (tabId === activeTabId) return
    setActiveTabId(tabId)
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
    clearAllDoneGraceTimers()
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
      className="fixed inset-0 lg:inset-auto lg:top-0 lg:right-0 lg:h-dvh bg-slate-950 lg:border-l border-slate-800 z-50 flex flex-col"
      style={{ ['--chat-w' as string]: `${chatWidth}px` }}
    >
      <style>{`@media (min-width: 1024px) { [data-tour="chat"] { width: var(--chat-w) !important; } }`}</style>
      {/* Resize handle (hidden on mobile where chat is full-screen) */}
      <div
        onMouseDown={handleMouseDown}
        className="hidden lg:block absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-blue-500/30 transition-colors z-10"
      />

      <div className="flex items-center justify-between p-4 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Icon name="chat" className="text-blue-400" />
          <span className="font-bold text-white">{displayOsName} Chat</span>
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

      <div className="flex-1 overflow-y-auto px-4 lg:px-10 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 px-6">
            <Icon name="chat" className="text-5xl text-slate-700 mb-3" />
            <p className="text-slate-400 text-sm mb-1">
              Talking to <span className={MODEL_COLORS[defaultChatModel] ?? 'text-blue-400'}>{defaultChatModel}</span>.
            </p>
            <p className="text-slate-600 text-xs mb-6">Switch with the button below, or type @claude / @gemini in your message.</p>
            <div className="flex flex-wrap justify-center gap-2 max-w-sm">
              {[
                { icon: 'calendar_month', text: "What's on my calendar today?" },
                { icon: 'checklist', text: 'Help me plan my week' },
                { icon: 'bolt', text: 'Break a project into tasks' },
                { icon: 'summarize', text: 'Write a status update from my recent work' },
              ].map((s) => (
                <button
                  key={s.text}
                  onClick={() => sendMessage(s.text)}
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800/60 hover:bg-slate-700/80 border border-slate-700 rounded-lg text-xs text-slate-300 hover:text-white transition-colors"
                >
                  <Icon name={s.icon} className="text-sm text-blue-400" />
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        )}
        {(() => {
          // Build a flat render list. Thread replies are injected inline
          // under their root bubble, grouped and collapsible.
          // Root messages: thread_id == null/undefined.
          // Thread messages: thread_id == some root message id.

          // Index thread children by their thread root id.
          const threadMap = new Map<string, Message[]>()
          for (const m of messages) {
            if (m.thread_id) {
              const arr = threadMap.get(m.thread_id) ?? []
              arr.push(m)
              threadMap.set(m.thread_id, arr)
            }
          }

          // Helper: render a single bubble. isThread=true means it is
          // inside a thread block (indented, left-border style).
          // inBroadcastColumn=true means this bubble is one of the two
          // cells inside a "grid grid-cols-2" broadcast wrapper, so it
          // must stretch to fill its column instead of sizing to its
          // content.
          const renderBubble = (
            msg: Message,
            globalIdx: number,
            isThread: boolean,
            inBroadcastColumn: boolean = false,
          ) => {
            const isEmpty = !msg.content && !msg.toolCalls?.length && !msg.gifUrl && !msg.imageUrl
            if (isEmpty && msg.role === 'assistant' && multiAiStatus && globalIdx === messages.length - 1) return null
            // Suppress truly empty finalized assistant bubbles. These appear when
            // sendMessage pushes a placeholder that was never filled (e.g. the
            // second bubble in a pure tool-use turn). Only suppress when not
            // streaming so we never hide a bubble that is still waiting for tokens.
            if (isEmpty && msg.role === 'assistant' && !isStreaming && globalIdx !== messages.length - 1) return null
            return (
              <div
                key={msg.id}
                id={`msg-${msg.id}`}
                data-testid={`bubble-${msg.id}`}
                className={`group transition-all rounded-xl ${inBroadcastColumn ? 'min-w-0 w-full' : ''} ${msg.role === 'user' ? 'flex flex-col items-end' : ''} ${isThread ? 'ml-2' : ''}`}
              >
                {/* Reply context within a thread */}
                {msg.replyTo && isThread && (
                  <ReplyPreview
                    message={findMessage(msg.replyTo)}
                    onClick={() => scrollToMessage(msg.replyTo!)}
                  />
                )}
                {/* Reply context on root messages (non-threaded) */}
                {msg.replyTo && !isThread && (
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
                    {/* Auto-matched template badge. Hidden while the assistant bubble
                        is still in the pure thinking state (streaming with no visible
                        content yet) so nothing renders beside the thinking indicator. */}
                    {globalIdx === messages.length - 1 && activeTemplate && msg.content?.trim() && (
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

                <div className={`relative ${msg.role === 'user' ? 'ml-auto max-w-[75%] w-fit' : inBroadcastColumn ? 'w-full min-w-0' : 'max-w-[85%] w-fit'}`}>
                  <div
                    className={
                      msg.role === 'user'
                        ? 'inline-block bg-blue-500/20 text-blue-100 px-4 py-2.5 rounded-2xl rounded-br-sm text-sm break-words overflow-hidden'
                        : `${inBroadcastColumn ? 'block w-full' : 'inline-block'} border px-4 py-3 rounded-xl text-sm text-slate-300 overflow-hidden break-words ${
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
                        {msg.content?.trim() && (
                          <CollapsibleText text={msg.content} isLast={globalIdx === messages.length - 1} streaming={isStreaming} />
                        )}
                        {msg.isError && globalIdx === messages.length - 1 && (
                          <button
                            type="button"
                            data-testid="retry-last-turn"
                            onClick={retryLastTurn}
                            className="mt-2 inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/40 text-blue-200 text-xs font-medium transition-colors"
                          >
                            <Icon name="refresh" className="text-sm" />
                            Retry
                          </button>
                        )}
                        {globalIdx === messages.length - 1 && !multiAiStatus && (isStreaming || placeholderAwaitingServer) && !msg.toolCalls?.length && !activeStreamingBubbleIds.has(msg.id) && (
                          <ThinkingDots />
                        )}
                        {/* Parallel broadcast case: every bubble with an
                            active turn_start but no tokens yet gets its
                            own thinking indicator, not just the last
                            one. Without this the second model in the
                            fan-out appears as a blank bubble while only
                            the first shows activity. */}
                        {activeStreamingBubbleIds.has(msg.id) && !msg.content?.trim() && !msg.toolCalls?.length && (
                          <ThinkingDots />
                        )}
                        {isStreaming && globalIdx === messages.length - 1 && (msg.toolCalls?.some(tc => tc.result === undefined)) && (
                          <ThinkingDots />
                        )}
                        {/* Fallback: stream is confirmed done and the bubble has no visible text.
                            Three sub-cases:
                            1. Tool-only turn: tools ran but Claude sent no follow-up text.
                               Show "Done." below the tool blocks so the label is never empty.
                            2. Completely empty last bubble: can briefly flash while waiting
                               for the first token. Show "Done." so nothing is ever blank.
                            3. Whitespace-only content: a '\n' token makes msg.content truthy
                               but renderMarkdown returns nothing visible. Treat the same as
                               empty so the bubble never shows just a label with no text.
                            Uses confirmedDoneIds (500ms grace window) instead of !isStreaming
                            so providers like Gemini that flush `done` before the last text
                            tokens never cause a visible "Done." flash. The label only renders
                            after the grace window expires with no new tokens.
                            All cases use data-testid="tool-only-done" for tests. */}
                        {confirmedDoneIds.has(msg.id) && !msg.content?.trim() && (
                          <span
                            className="text-slate-500 text-xs italic"
                            data-testid="tool-only-done"
                          >
                            Done.
                          </span>
                        )}
                      </>
                    )}
                  </div>

                  {/* Reaction pills */}
                  {msg.reactions && Object.keys(msg.reactions).length > 0 && (
                    <div className={`flex flex-wrap gap-1 mt-0.5 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
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

                  {/* Reply and reaction buttons on hover */}
                  <div className={`${msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'} opacity-0 group-hover:opacity-100 mt-0.5 transition-all`}>
                    <div className="flex items-center gap-0.5 z-10">
                      <button
                        onClick={() => handleReply(msg.id)}
                        className="p-1 text-slate-600 hover:text-blue-400 transition-colors"
                        title="Reply"
                        data-testid={`reply-btn-${msg.id}`}
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
                </div>
              </div>
            )
          }

          // Render root messages. For each root message that has thread
          // children, render them in a collapsible block directly below.
          // Broadcast pairs: when two adjacent assistant root messages
          // are tagged with different models (claude + gemini) they form
          // one broadcast turn. Wrap them in a 2-column grid so both
          // bubbles sit at the same top and stream independently down
          // each column, instead of stacking vertically.
          const rootMessages = messages.filter(m => !m.thread_id)
          const rendered: React.ReactNode[] = []
          let i = 0
          while (i < rootMessages.length) {
            const msg = rootMessages[i]
            const next = rootMessages[i + 1]
            const isBroadcastPair =
              msg.role === 'assistant' &&
              next?.role === 'assistant' &&
              !!msg.model &&
              !!next.model &&
              msg.model !== next.model &&
              (msg.model === 'claude' || msg.model === 'gemini') &&
              (next.model === 'claude' || next.model === 'gemini')

            if (isBroadcastPair) {
              // Put claude on the left, gemini on the right regardless
              // of the order the backend emitted turn_start frames in.
              const claudeMsg = msg.model === 'claude' ? msg : next
              const geminiMsg = msg.model === 'gemini' ? msg : next
              const claudeIdx = messages.indexOf(claudeMsg)
              const geminiIdx = messages.indexOf(geminiMsg)
              rendered.push(
                <div
                  key={`broadcast-${claudeMsg.id}-${geminiMsg.id}`}
                  data-testid="broadcast-pair"
                  className="grid grid-cols-2 gap-3 items-start w-full"
                >
                  {renderBubble(claudeMsg, claudeIdx, false, true)}
                  {renderBubble(geminiMsg, geminiIdx, false, true)}
                </div>,
              )
              i += 2
              continue
            }

            const globalIdx = messages.indexOf(msg)
            const threadChildren = threadMap.get(msg.id) ?? []
            const isCollapsed = collapsedThreads.has(msg.id)
            rendered.push(
              <div key={msg.id}>
                {renderBubble(msg, globalIdx, false)}

                {/* Thread block */}
                {threadChildren.length > 0 && (
                  <div className="mt-1 ml-3 border-l-2 border-slate-700 pl-3" data-testid={`thread-block-${msg.id}`}>
                    <button
                      onClick={() => toggleThread(msg.id)}
                      className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-blue-400 transition-colors mb-1"
                      data-testid={`thread-toggle-${msg.id}`}
                      aria-expanded={!isCollapsed}
                    >
                      <Icon name={isCollapsed ? 'chevron_right' : 'expand_more'} className="text-xs" />
                      {isCollapsed
                        ? `${threadChildren.length} ${threadChildren.length === 1 ? 'reply' : 'replies'}`
                        : 'Hide replies'}
                    </button>
                    {!isCollapsed && (
                      <div className="space-y-2">
                        {threadChildren.map((child) => {
                          const childIdx = messages.indexOf(child)
                          return renderBubble(child, childIdx, true)
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>,
            )
            i += 1
          }
          return rendered
        })()}
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
          <button
            onClick={() => { setSideBySideEnabled(false); setDefaultChatModel('claude'); }}
            className={`shrink-0 px-3 py-1.5 rounded-full border text-xs font-medium transition-all cursor-pointer hover:scale-105 ${
              !sideBySideEnabled && defaultChatModel === 'claude'
                ? 'bg-blue-500/15 border-blue-500/40 text-blue-300'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
            }`}
            title="Chat with Claude only"
            data-testid="chat-pill-claude"
          >
            Claude
          </button>
          <button
            onClick={() => { setSideBySideEnabled(false); setDefaultChatModel('gemini'); }}
            className={`shrink-0 px-3 py-1.5 rounded-full border text-xs font-medium transition-all cursor-pointer hover:scale-105 ${
              !sideBySideEnabled && defaultChatModel === 'gemini'
                ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
            }`}
            title="Chat with Gemini only"
            data-testid="chat-pill-gemini"
          >
            Gemini
          </button>
          <button
            onClick={() => setSideBySideEnabled(true)}
            className={`shrink-0 relative px-3 py-1.5 rounded-full border text-xs font-medium transition-all cursor-pointer hover:scale-105 ${
              sideBySideEnabled
                ? 'bg-purple-500/15 border-purple-500/40 text-purple-300'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
            } ${allPillPulse ? 'ring-2 ring-purple-400/70 animate-pulse' : ''}`}
            title="Send to Claude and Gemini side by side"
            data-testid="chat-pill-all"
            data-pulse={allPillPulse ? 'true' : undefined}
          >
            All
            {allPillPulse && (
              <span
                data-testid="chat-pill-all-new-badge"
                className="absolute -top-1.5 -right-1.5 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-purple-500 text-white shadow-md"
              >
                New
              </span>
            )}
          </button>
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
            className={`hidden sm:block p-2 transition-colors rounded-lg ${showGiphy ? 'text-blue-400 bg-blue-500/10' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'}`}
            title="Search GIFs"
          >
            <Icon name="gif_box" className="text-lg" />
          </button>
          {speechSupported && (
            <button
              onClick={toggleSpeech}
              className={`p-2 transition-colors rounded-lg ${isListening ? 'text-red-400 bg-red-500/10 animate-pulse' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'}`}
              title={isListening ? 'Stop listening' : 'Voice input'}
            >
              <Icon name="mic" className="text-lg" />
            </button>
          )}
          <button
            onClick={handleSend}
            disabled={!input.trim() && !pendingImage}
            className="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
