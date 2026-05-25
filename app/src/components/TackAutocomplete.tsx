import { useState, useEffect, useRef } from 'react'
import { api } from '../lib/api'

interface Suggestion {
  value: string
  type: 'command' | 'query' | 'needle' | 'focus'
}

interface TackAutocompleteProps {
  inputValue: string
  onSelect: (suggestion: string) => void
  keyHandlerRef: React.MutableRefObject<((e: React.KeyboardEvent) => boolean) | null>
}

const STATIC_SUGGESTIONS: Suggestion[] = [
  { value: ':compile', type: 'command' },
  { value: ':status', type: 'command' },
  { value: '/help', type: 'query' },
  { value: '→needle', type: 'needle' },
  { value: '::target', type: 'focus' },
]

const TRIGGER_RE = /^(:|\/|->|→|::)/

const TYPE_COLORS: Record<Suggestion['type'], string> = {
  command: 'text-blue-600 dark:text-blue-400 bg-blue-500/10',
  query: 'text-purple-600 dark:text-purple-400 bg-purple-500/10',
  needle: 'text-orange-600 dark:text-orange-400 bg-orange-500/10',
  focus: 'text-pink-600 dark:text-pink-400 bg-pink-500/10',
}

function staticFallback(q: string): Suggestion[] {
  const lower = q.toLowerCase()
  const matched = STATIC_SUGGESTIONS.filter(s => s.value.toLowerCase().startsWith(lower))
  return matched.length ? matched : STATIC_SUGGESTIONS
}

export default function TackAutocomplete({ inputValue, onSelect, keyHandlerRef }: TackAutocompleteProps): React.ReactElement {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const open = suggestions.length > 0

  useEffect(() => {
    if (!TRIGGER_RE.test(inputValue)) {
      setSuggestions([])
      return
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await api.get(`/api/tack/suggestions?q=${encodeURIComponent(inputValue)}`)
        if (Array.isArray(data) && data.length > 0) {
          setSuggestions(data as Suggestion[])
        } else {
          setSuggestions(staticFallback(inputValue))
        }
      } catch {
        setSuggestions(staticFallback(inputValue))
      }
      setActiveIndex(0)
    }, 150)

    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [inputValue])

  useEffect(() => {
    if (!open) {
      keyHandlerRef.current = null
      return
    }
    keyHandlerRef.current = (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex(i => (i + 1) % suggestions.length)
        return true
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex(i => (i - 1 + suggestions.length) % suggestions.length)
        return true
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        const s = suggestions[activeIndex]
        if (s) {
          e.preventDefault()
          onSelect(s.value)
          setSuggestions([])
          return true
        }
      }
      if (e.key === 'Escape') {
        setSuggestions([])
        return true
      }
      return false
    }
  }, [open, suggestions, activeIndex, onSelect, keyHandlerRef])

  if (!open) return <></>

  return (
    <div
      className="absolute bottom-full left-0 right-0 mb-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg overflow-hidden z-50"
      data-testid="tack-autocomplete"
    >
      {suggestions.map((s, i) => (
        <button
          key={s.value}
          data-testid={`tack-suggestion-${i}`}
          className={`w-full text-left px-3 py-1.5 text-sm flex items-center gap-2 transition-colors ${i === activeIndex ? 'bg-slate-100 dark:bg-slate-800' : 'hover:bg-slate-100 dark:hover:bg-slate-800/50'}`}
          onMouseDown={(e) => { e.preventDefault(); onSelect(s.value); setSuggestions([]) }}
        >
          <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${TYPE_COLORS[s.type]}`}>{s.type}</span>
          <span className="text-slate-700 dark:text-slate-300 font-mono">{s.value}</span>
        </button>
      ))}
    </div>
  )
}
