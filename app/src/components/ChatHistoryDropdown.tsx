import { useState, useEffect, useRef } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface ChatTab {
  id: string
  name: string
  messages: ChatMessage[]
}

interface ChatHistory {
  tabs: ChatTab[]
  active_tab_id: string
}

export default function ChatHistoryDropdown() {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const fetchMessages = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.get<ChatHistory>('/chat/history')
      const allMessages: ChatMessage[] = []
      for (const tab of data.tabs) {
        allMessages.push(...(tab.messages || []))
      }
      const lastFive = allMessages.slice(-5)
      setMessages(lastFive)
    } catch {
      setError('Failed to load chat history')
    } finally {
      setLoading(false)
    }
  }

  const handleToggle = () => {
    if (!isOpen) {
      fetchMessages()
    }
    setIsOpen(!isOpen)
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={handleToggle}
        className="p-2.5 sm:p-2 text-slate-400 hover:text-blue-400 transition-all"
        title="Chat history"
      >
        <Icon name="chat_history" />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />
          <div className="absolute right-0 top-full mt-2 w-80 bg-slate-900 border border-slate-800 rounded-xl shadow-xl z-50 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Last 5 Messages</span>
              <button onClick={() => setIsOpen(false)} className="text-slate-500 hover:text-white">
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="max-h-96 overflow-y-auto">
              {loading ? (
                <div className="p-6 text-center">
                  <Icon name="hourglass_empty" size={32} className="text-slate-700 mb-2 inline-block" />
                  <p className="text-sm text-slate-500">Loading messages...</p>
                </div>
              ) : error ? (
                <div className="p-6 text-center">
                  <Icon name="error" size={32} className="text-red-500 mb-2 inline-block" />
                  <p className="text-sm text-red-400">{error}</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="p-6 text-center">
                  <Icon name="chat_bubble_outline" size={32} className="text-slate-700 mb-2 inline-block" />
                  <p className="text-sm text-slate-500">No messages yet</p>
                </div>
              ) : (
                <div className="divide-y divide-slate-800/50">
                  {messages.map((msg, idx) => (
                    <div key={idx} className="px-4 py-3 hover:bg-slate-800/30 transition-colors">
                      <p className="text-xs font-semibold text-slate-400 mb-1">
                        {msg.role === 'user' ? 'You' : 'Assistant'}
                      </p>
                      <p className="text-sm text-slate-300 line-clamp-3">
                        {msg.content}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
