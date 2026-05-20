interface Props {
  plan: string
  onConfirm: () => void
  onCancel: () => void
}

export function PlanBanner({ plan, onConfirm, onCancel }: Props) {
  return (
    <div
      data-testid="plan-banner"
      className="inline-flex flex-col gap-3 px-4 py-3 rounded-xl border border-amber-500/30 bg-amber-500/5 text-sm text-slate-300 max-w-[85%]"
    >
      <div>
        <span className="text-xs text-amber-400 block mb-2 font-medium">Plan to run</span>
        <div className="text-slate-200 text-xs whitespace-pre-wrap leading-relaxed">{plan}</div>
      </div>
      <div className="flex gap-2">
        <button
          data-testid="plan-banner-confirm"
          onClick={onConfirm}
          className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium transition-colors"
        >
          Confirm
        </button>
        <button
          data-testid="plan-banner-cancel"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-white text-xs font-medium transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
