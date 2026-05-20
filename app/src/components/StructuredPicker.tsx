import { useState } from 'react'

interface Option {
  label: string
  description?: string
}

interface Props {
  question: string
  options: Option[]
  multiSelect?: boolean
  onSelect: (label: string) => void
}

export function StructuredPicker({ question, options, multiSelect = false, onSelect }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitted, setSubmitted] = useState(false)
  const [showOtherInput, setShowOtherInput] = useState(false)
  const [otherText, setOtherText] = useState('')

  function handleChipClick(label: string) {
    if (submitted) return
    if (label === 'Other') {
      setShowOtherInput(true)
      return
    }
    if (multiSelect) {
      setSelected(prev => {
        const next = new Set(prev)
        if (next.has(label)) next.delete(label)
        else next.add(label)
        return next
      })
    } else {
      setSelected(new Set([label]))
      setSubmitted(true)
      onSelect(label)
    }
  }

  function handleDone() {
    if (selected.size === 0) return
    setSubmitted(true)
    onSelect(Array.from(selected).join(', '))
  }

  function handleOtherSend() {
    if (!otherText.trim()) return
    setSubmitted(true)
    onSelect(otherText.trim())
  }

  const allOptions = [...options, { label: 'Other' }]

  return (
    <div
      data-testid="structured-picker"
      className="my-3 rounded-xl border border-slate-700/60 bg-slate-900/60 backdrop-blur-sm p-4 space-y-3"
    >
      <p className="text-sm text-slate-300 font-medium">{question}</p>
      <div className="flex flex-wrap gap-2">
        {allOptions.map(opt => {
          const isSelected = selected.has(opt.label)
          return (
            <button
              key={opt.label}
              data-testid={`picker-option-${opt.label}`}
              data-selected={isSelected ? 'true' : undefined}
              onClick={() => handleChipClick(opt.label)}
              disabled={submitted}
              className={[
                'inline-flex flex-col items-start px-3 py-2 rounded-lg border text-sm text-left transition-colors',
                submitted
                  ? isSelected
                    ? 'border-blue-500/80 bg-blue-500/20 text-blue-200 cursor-default'
                    : 'border-slate-700/40 bg-slate-800/30 text-slate-500 cursor-default'
                  : isSelected
                  ? 'border-blue-500/60 bg-blue-500/10 text-blue-200'
                  : 'border-slate-600/60 bg-slate-800/60 text-slate-200 hover:border-blue-500/60 hover:bg-blue-500/10 hover:text-blue-200',
              ].join(' ')}
            >
              <span className="font-medium">{opt.label}</span>
              {opt.description && (
                <span className="text-xs text-slate-400 mt-0.5">{opt.description}</span>
              )}
            </button>
          )
        })}
      </div>
      {showOtherInput && !submitted && (
        <div className="flex gap-2 mt-2">
          <input
            data-testid="picker-other-input"
            type="text"
            value={otherText}
            onChange={e => setOtherText(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleOtherSend()}
            placeholder="Type your answer…"
            className="flex-1 rounded-lg bg-slate-800/60 border border-slate-600/60 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/60"
            autoFocus
          />
          <button
            data-testid="picker-other-send"
            onClick={handleOtherSend}
            className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm transition-colors"
          >
            Send
          </button>
        </div>
      )}
      {multiSelect && !submitted && (
        <button
          data-testid="picker-done"
          onClick={handleDone}
          disabled={selected.size === 0}
          className="mt-1 px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm transition-colors"
        >
          Done
        </button>
      )}
    </div>
  )
}
