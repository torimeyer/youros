import { useState } from 'react'

export interface PRFlag {
  title: string
  severity: 'high' | 'medium' | 'low'
  description: string
  file?: string | null
}

export interface PRWalkthroughEntry {
  file: string
  change_type: string
  description: string
}

export interface PRReviewData {
  summary: string
  file_count: number
  additions: number
  deletions: number
  truncated: boolean
  walkthrough: PRWalkthroughEntry[]
  flags: PRFlag[]
  pr_url: string
  pr_title: string
  pr_number: number
  owner: string
  repo: string
}

const SEVERITY_STYLES: Record<string, string> = {
  high: 'bg-red-500/10 border-red-500/40 text-red-300',
  medium: 'bg-amber-500/10 border-amber-500/40 text-amber-300',
  low: 'bg-blue-500/10 border-blue-500/40 text-blue-300',
}

const SEVERITY_DOT: Record<string, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-blue-400',
}

const CHANGE_TYPE_COLORS: Record<string, string> = {
  added: 'text-green-400',
  deleted: 'text-red-400',
  modified: 'text-amber-400',
  renamed: 'text-blue-400',
}

function FlagCard({ flag }: { flag: PRFlag }) {
  const sev = flag.severity || 'low'
  return (
    <div
      data-testid={`pr-flag-${sev}`}
      className={`flex items-start gap-2 px-3 py-2 rounded-lg border text-xs ${SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.low}`}
    >
      <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${SEVERITY_DOT[sev] ?? SEVERITY_DOT.low}`} />
      <div className="min-w-0">
        <span className="font-semibold">{flag.title}</span>
        {flag.file && (
          <span className="ml-1.5 opacity-60 font-mono text-[10px]">{flag.file}</span>
        )}
        <p className="mt-0.5 opacity-80">{flag.description}</p>
      </div>
    </div>
  )
}

export function PRReviewCard({ data }: { data: PRReviewData }) {
  const [walkthroughOpen, setWalkthroughOpen] = useState(false)

  const highFlags = data.flags.filter(f => f.severity === 'high')
  const otherFlags = data.flags.filter(f => f.severity !== 'high')

  return (
    <div data-testid="pr-review-card" className="flex flex-col gap-3 mt-1">
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <a
          href={data.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-semibold text-blue-400 hover:underline"
          data-testid="pr-review-link"
        >
          #{data.pr_number} {data.pr_title}
        </a>
        <span className="text-[10px] text-slate-500 font-mono">{data.owner}/{data.repo}</span>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-3 text-[11px] text-slate-400">
        <span>{data.file_count} {data.file_count === 1 ? 'file' : 'files'}</span>
        <span className="text-green-400">+{data.additions}</span>
        <span className="text-red-400">-{data.deletions}</span>
        {data.truncated && (
          <span className="text-amber-400 font-medium">partial review (large PR)</span>
        )}
      </div>

      {/* Summary */}
      <div className="text-sm text-slate-300 leading-relaxed" data-testid="pr-review-summary">
        {data.summary}
      </div>

      {/* Risk flags — high severity first */}
      {data.flags.length > 0 && (
        <div className="flex flex-col gap-1.5" data-testid="pr-review-flags">
          {highFlags.map((f, i) => <FlagCard key={i} flag={f} />)}
          {otherFlags.map((f, i) => <FlagCard key={i + highFlags.length} flag={f} />)}
        </div>
      )}

      {/* File walkthrough — collapsed by default */}
      {data.walkthrough.length > 0 && (
        <div>
          <button
            data-testid="pr-review-walkthrough-toggle"
            onClick={() => setWalkthroughOpen(o => !o)}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
          >
            <span
              className={`inline-block transition-transform ${walkthroughOpen ? 'rotate-90' : ''}`}
            >
              ▶
            </span>
            {walkthroughOpen ? 'Hide' : 'Show'} file walkthrough ({data.walkthrough.length} files)
          </button>

          {walkthroughOpen && (
            <div
              className="mt-2 flex flex-col gap-1.5 border-l-2 border-slate-700 pl-3"
              data-testid="pr-review-walkthrough"
            >
              {data.walkthrough.map((entry, i) => (
                <div key={i} className="text-xs">
                  <span
                    className={`font-mono ${CHANGE_TYPE_COLORS[entry.change_type] ?? 'text-slate-400'}`}
                  >
                    {entry.file}
                  </span>
                  <span className="text-slate-400 ml-2">{entry.description}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
