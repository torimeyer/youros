import { useState, useEffect, useRef } from 'react'
import Icon from './Icon'

interface InlineTextPromptProps {
  label: string
  placeholder: string
  prefix: string
  onSubmit: (full: string) => void
  onCancel: () => void
}

export function InlineTextPrompt({ label, placeholder, prefix, onSubmit, onCancel }: InlineTextPromptProps) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const submit = () => {
    const trimmed = value.trim()
    if (trimmed) onSubmit(prefix + trimmed)
  }

  return (
    <div
      data-testid="inline-text-prompt"
      className="inline-flex flex-col gap-2 px-4 py-3 rounded-xl border border-blue-500/30 bg-blue-500/5 text-sm text-slate-700 dark:text-slate-300 max-w-[85%]"
    >
      <span className="text-xs text-slate-600 dark:text-slate-400">{label}</span>
      <div className="flex gap-2">
        <input
          ref={inputRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.preventDefault(); submit() }
          }}
          placeholder={placeholder}
          className="flex-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-800 dark:text-slate-200 placeholder-slate-500 outline-none focus:border-blue-500/50 min-w-0"
        />
        <button
          onClick={submit}
          disabled={!value.trim()}
          data-testid="inline-text-prompt-submit"
          className="px-3 py-1.5 rounded-lg bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/40 text-blue-700 dark:text-blue-300 text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Icon name="send" className="text-sm" />
        </button>
      </div>
    </div>
  )
}
