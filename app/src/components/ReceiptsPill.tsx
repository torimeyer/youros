/** Receipts gate pill — shown below assistant messages (→1534).
 *
 * "missing": amber warning — trigger word with no evidence.
 * "present": subtle green — trigger word backed by a commit/output/file ref.
 * Renders nothing for any other value.
 */
export type ReceiptsStatus = 'present' | 'missing'

interface Props {
  status: ReceiptsStatus | undefined
}

const TOOLTIP =
  "This message used a 'done' / 'fixed' / 'landed' word but didn't show " +
  'a commit hash, command output, file quote, or testid. Ask the model for evidence.'

export default function ReceiptsPill({ status }: Props) {
  if (!status) return null

  if (status === 'missing') {
    return (
      <div
        data-testid="receipts-pill-missing"
        title={TOOLTIP}
        className="mt-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 border border-amber-500/30 text-amber-300 cursor-default select-none"
      >
        <span aria-hidden="true">⚠</span>
        <span>No receipts</span>
      </div>
    )
  }

  if (status === 'present') {
    return (
      <div
        data-testid="receipts-pill-present"
        title="This message includes a commit hash, test output, or file reference as evidence."
        className="mt-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 cursor-default select-none"
      >
        <span aria-hidden="true">✓</span>
        <span>receipts</span>
      </div>
    )
  }

  return null
}
