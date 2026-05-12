import { useState, useRef, useEffect } from 'react';
import Icon from './Icon';
import type { Gem } from '../pages/MyGems';

interface HistoryEntry {
  role: 'user' | 'model';
  text: string;
}

interface Props {
  gem: Gem;
  onClose: () => void;
}

export default function GemChatPanel({ gem, onClose }: Props) {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (bottomRef.current?.scrollIntoView) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history, streamingText]);

  const send = async () => {
    const msg = input.trim();
    if (!msg || sending) return;

    const outgoing: HistoryEntry = { role: 'user', text: msg };
    const nextHistory = [...history, outgoing];
    setHistory(nextHistory);
    setInput('');
    setSending(true);
    setStreamingText('');

    try {
      const res = await fetch(`/api/gems/${gem.id}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ message: msg, history }),
      });

      if (!res.ok || !res.body) {
        setHistory((h) => [...h, { role: 'model', text: 'Something went wrong. Please try again.' }]);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const frame = JSON.parse(raw);
            if (frame.type === 'token' && typeof frame.data === 'string') {
              accumulated += frame.data;
              setStreamingText(accumulated);
            } else if (frame.type === 'done') {
              break;
            }
          } catch {
            // skip malformed frames
          }
        }
      }

      const finalText = accumulated || 'No response.';
      setStreamingText('');
      setHistory((h) => [...h, { role: 'model', text: finalText }]);
    } catch {
      setStreamingText('');
      setHistory((h) => [...h, { role: 'model', text: 'Connection error. Please try again.' }]);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Chat with ${gem.name}`}
      data-testid="gem-chat-panel"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-lg bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Icon name="auto_awesome" size={16} className="text-emerald-400" />
            <span className="text-sm font-semibold text-white truncate">{gem.name}</span>
          </div>
          <button
            data-testid="gem-chat-close"
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 min-h-0">
          {history.length === 0 && !streamingText && (
            <p className="text-xs text-slate-500 text-center py-6" data-testid="gem-chat-empty">
              Start a conversation with {gem.name}
            </p>
          )}

          {history.map((entry, i) => (
            <div
              key={i}
              data-testid={entry.role === 'user' ? `gem-chat-user-bubble-${i}` : `gem-chat-assistant-bubble-${i}`}
              className={`flex ${entry.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] px-3 py-2 rounded-xl text-sm border ${
                  entry.role === 'user'
                    ? 'bg-slate-700 border-slate-600 text-slate-100'
                    : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                }`}
              >
                {entry.text}
              </div>
            </div>
          ))}

          {streamingText && (
            <div className="flex justify-start" data-testid="gem-chat-streaming">
              <div className="max-w-[80%] px-3 py-2 rounded-xl text-sm border bg-emerald-500/10 border-emerald-500/30 text-emerald-400">
                {streamingText}
              </div>
            </div>
          )}

          {sending && !streamingText && (
            <div className="flex justify-start" data-testid="gem-chat-thinking">
              <div className="px-3 py-2 rounded-xl text-sm border bg-emerald-500/10 border-emerald-500/30 text-emerald-400">
                <span className="animate-pulse">thinking...</span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-4 pb-4 pt-2 border-t border-slate-800 flex-shrink-0">
          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              data-testid="gem-chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message…"
              rows={1}
              disabled={sending}
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500/40 transition-colors resize-none disabled:opacity-50"
            />
            <button
              data-testid="gem-chat-send"
              onClick={send}
              disabled={!input.trim() || sending}
              className="flex-shrink-0 p-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
              aria-label="Send"
            >
              <Icon name="send" size={16} className="text-white" />
            </button>
          </div>
          <p className="text-[10px] text-slate-600 mt-1.5">Enter to send · Shift+Enter for new line</p>
        </div>
      </div>
    </div>
  );
}
