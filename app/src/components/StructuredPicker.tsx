interface Option {
  label: string
  description?: string
}

interface Props {
  question: string
  options: Option[]
  onSelect: (label: string) => void
}

export function StructuredPicker({ question, options, onSelect }: Props) {
  return (
    <div
      data-testid="structured-picker"
      className="my-3 rounded-xl border border-slate-700/60 bg-slate-900/60 backdrop-blur-sm p-4 space-y-3"
    >
      <p className="text-sm text-slate-300 font-medium">{question}</p>
      <div className="flex flex-wrap gap-2">
        {options.map(opt => (
          <button
            key={opt.label}
            data-testid={`picker-option-${opt.label}`}
            onClick={() => onSelect(opt.label)}
            className="inline-flex flex-col items-start px-3 py-2 rounded-lg border border-slate-600/60 bg-slate-800/60 text-sm text-slate-200 hover:border-blue-500/60 hover:bg-blue-500/10 hover:text-blue-200 transition-colors text-left"
          >
            <span className="font-medium">{opt.label}</span>
            {opt.description && (
              <span className="text-xs text-slate-400 mt-0.5">{opt.description}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
