import { useState, useEffect, useRef, useCallback } from 'react'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { useWebSocket } from '../hooks/useWebSocket'

const MODEL_COLORS: Record<string, string> = {
  claude: 'text-blue-400',
  gemini: 'text-emerald-400',
  gpt: 'text-purple-400',
}

const MODEL_BG: Record<string, string> = {
  claude: 'bg-blue-500/10 border-blue-500/30',
  gemini: 'bg-emerald-500/10 border-emerald-500/30',
  gpt: 'bg-purple-500/10 border-purple-500/30',
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
}

interface ToolCall {
  id: string
  tool: string
  input: Record<string, unknown>
  result?: string
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  model?: string
  toolCalls?: ToolCall[]
  replyTo?: string
  gifUrl?: string
}

interface GiphyResult {
  id: string
  url: string
  preview: string
  title: string
}

function genId(): string {
  return crypto.randomUUID()
}

function ToolCallBlock({ call }: { call: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const label = TOOL_LABELS[call.tool] ?? call.tool

  let summary = ''
  if (call.input.path) summary = String(call.input.path)
  else if (call.input.command) summary = String(call.input.command)
  else if (call.input.pattern) summary = String(call.input.pattern)
  else if (call.input.title) summary = String(call.input.title)
  else if (call.input.task_id) summary = String(call.input.task_id)

  return (
    <div className="my-1.5 border border-slate-700 rounded-lg overflow-hidden bg-slate-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-300 hover:bg-slate-800/50 transition-colors"
      >
        <Icon name={expanded ? 'expand_more' : 'chevron_right'} className="text-sm" />
        <span className="font-medium text-amber-400">{label}</span>
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
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [currentModel, setCurrentModel] = useState<string | null>(null)
  const [toolsEnabled, setToolsEnabled] = useState(true)
  const [replyingTo, setReplyingTo] = useState<string | null>(null)
  const [showGiphy, setShowGiphy] = useState(false)
  const [giphyInitialSearch, setGiphyInitialSearch] = useState('')
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
    } else if (lastMessage.type === 'token') {
      setMessages(prev => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === 'assistant') {
          updated[updated.length - 1] = { ...last, content: last.content + lastMessage.data }
        }
        return updated
      })
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
    } else if (lastMessage.type === 'model_boundary') {
      setMessages(prev => [...prev, { id: genId(), role: 'assistant', content: '', model: '' }])
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
    } else if (lastMessage.type === 'error') {
      setIsStreaming(false)
      setCurrentModel(null)
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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when starting a reply
  useEffect(() => {
    if (replyingTo) inputRef.current?.focus()
  }, [replyingTo])

  // Resize handling
  const handleMouseDown = useCallback(() => {
    setIsResizing(true)
  }, [])

  useEffect(() => {
    if (!isResizing) return

    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX
      setChatWidth(newWidth)
    }

    const handleMouseUp = () => {
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
    }
  }, [isResizing, setChatWidth])

  if (!chatOpen) return null

  const sendMessage = (text: string) => {
    // Handle /giphy command
    if (text.trim().toLowerCase().startsWith('/giphy')) {
      const searchTerm = text.trim().slice(6).trim()
      setGiphyInitialSearch(searchTerm)
      setShowGiphy(true)
      return
    }

    if (!text.trim() || isStreaming) return
    const userMessage: Message = {
      id: genId(),
      role: 'user',
      content: text.trim(),
      replyTo: replyingTo || undefined,
    }

    const mentionMatch = text.match(/@(\w+)/i)
    const detectedModel = mentionMatch ? mentionMatch[1].toLowerCase() : defaultChatModel

    const assistantMessage: Message = { id: genId(), role: 'assistant', content: '', model: detectedModel }
    const updatedMessages = [...messages, userMessage]
    setMessages([...updatedMessages, assistantMessage])
    setIsStreaming(true)
    setCurrentModel(detectedModel)
    setReplyingTo(null)

    // Build the messages payload, including reply context if applicable
    const apiMessages = updatedMessages.map(m => ({
      role: m.role,
      content: m.gifUrl ? `[gif:${m.gifUrl}]` : (m.content || ''),
    }))
    if (userMessage.replyTo) {
      const repliedMsg = messages.find(m => m.id === userMessage.replyTo)
      if (repliedMsg) {
        const replyContent = repliedMsg.gifUrl ? '[GIF]' : repliedMsg.content
        const sender = repliedMsg.role === 'user' ? 'the user' : (repliedMsg.model || 'the assistant')
        // Prepend reply context to the last user message
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
    if (isStreaming) return
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
    if (!input.trim()) return
    sendMessage(input)
    setInput('')
  }

  const handleNewConversation = () => {
    setMessages([])
    setIsStreaming(false)
    setCurrentModel(null)
    setReplyingTo(null)
    setShowGiphy(false)
  }

  const handleReply = (messageId: string) => {
    setReplyingTo(messageId)
    setShowGiphy(false)
  }

  const scrollToMessage = (messageId: string) => {
    const el = document.getElementById(`msg-${messageId}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('ring-2', 'ring-blue-500/50')
      setTimeout(() => el.classList.remove('ring-2', 'ring-blue-500/50'), 1500)
    }
  }

  const renderText = (text: string) => {
    const parts = text.split(/(@\w+)/g)
    return parts.map((part, i) => {
      if (part.startsWith('@')) {
        const model = part.slice(1).toLowerCase()
        const color = MODEL_COLORS[model]
        if (color) {
          return <span key={i} className={`${color} font-medium`}>{part}</span>
        }
      }
      return part
    })
  }

  return (
    <div
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
            onClick={handleNewConversation}
            className="p-1 text-slate-400 hover:text-white transition-colors"
          >
            <Icon name="add" />
          </button>
          <button onClick={toggleChat} className="p-1 text-slate-400 hover:text-white transition-colors">
            <Icon name="close" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center py-8">
            <Icon name="chat" className="text-4xl text-slate-700 mb-2" />
            <p className="text-slate-500 text-sm">
              Messages go to <span className={MODEL_COLORS[defaultChatModel] ?? 'text-blue-400'}>{defaultChatModel}</span> by default.
            </p>
            <p className="text-slate-600 text-xs mt-1">Use @gemini or @gpt to talk to a different model. Change your default in Settings.</p>
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
              </div>
            )}

            <div className="relative">
              {/* Reply button on hover */}
              <button
                onClick={() => handleReply(msg.id)}
                className={`absolute ${msg.role === 'user' ? '-left-8' : '-right-8'} top-1 opacity-0 group-hover:opacity-100 p-1 text-slate-600 hover:text-blue-400 transition-all`}
                title="Reply"
              >
                <Icon name="reply" className="text-sm" />
              </button>

              <div
                className={
                  msg.role === 'user'
                    ? 'bg-blue-500/20 text-blue-100 px-4 py-2 rounded-2xl rounded-br-sm max-w-[80%] text-sm'
                    : `border px-4 py-3 rounded-xl text-sm text-slate-300 whitespace-pre-line ${
                        msg.model ? MODEL_BG[msg.model] ?? 'bg-slate-900 border-slate-800' : 'bg-slate-900 border-slate-800'
                      }`
                }
              >
                {/* GIF display */}
                {msg.gifUrl && (
                  <img
                    src={msg.gifUrl}
                    alt="GIF"
                    className="rounded-lg max-w-[250px] max-h-[200px]"
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
                    {msg.content || (isStreaming && i === messages.length - 1 && !msg.toolCalls?.length ? (
                      <span className="inline-flex items-center gap-1 py-1">
                        <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                      </span>
                    ) : null)}
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
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

      <div className="p-3 border-t border-slate-800">
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

        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              disabled={isStreaming}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-sm text-slate-300 outline-none focus:ring-2 focus:ring-blue-500/50 disabled:opacity-50"
              placeholder={replyingTo ? 'Type your reply...' : `Message ${defaultChatModel}... (/giphy to search GIFs)`}
            />
          </div>
          <button
            onClick={() => { setShowGiphy(!showGiphy); setGiphyInitialSearch('') }}
            className={`p-2 transition-colors rounded-lg ${showGiphy ? 'text-blue-400 bg-blue-500/10' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'}`}
            title="Search GIFs"
          >
            <Icon name="gif_box" className="text-lg" />
          </button>
          <button
            onClick={handleSend}
            disabled={isStreaming || !input.trim()}
            className="p-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isStreaming ? (
              <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Icon name="send" className="text-lg" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
