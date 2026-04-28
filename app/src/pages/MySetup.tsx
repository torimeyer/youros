import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import Icon from '../components/Icon'

interface SetupSummary {
  provider: string
  model: string
  connected_tools: string[]
  connected_tools_count: number
  top_skills: string[]
  agents_created: number
  agentfiles_count: number
}

function StatCard({ icon, label, value, sub }: {
  icon: string
  label: string
  value: string | number
  sub?: string
}) {
  return (
    <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-5 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-slate-400">
        <Icon name={icon} className="text-lg" />
        <span className="text-xs font-semibold uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-2xl font-bold text-slate-100">{value}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export default function MySetup() {
  const [data, setData] = useState<SetupSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    api.get<SetupSummary>('/my-setup/summary')
      .then((d) => { setData(d); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  async function handleCopyMarkdown() {
    try {
      const resp = await fetch('/api/my-setup/export?format=markdown')
      const text = await resp.text()
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // ignore
    }
  }

  async function handleDownloadConfig() {
    setDownloading(true)
    try {
      const resp = await fetch('/api/my-setup/export?format=json')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'my-setup.json'
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    } finally {
      setDownloading(false)
    }
  }

  function buildShareText(): string {
    if (!data) return ''
    const lines: string[] = []
    lines.push(`# My AI Setup`)
    lines.push(``)
    lines.push(`**Your AI assistant:** ${data.provider} (${data.model})`)
    if (data.connected_tools.length > 0) {
      lines.push(`**Connected tools:** ${data.connected_tools_count} (${data.connected_tools.join(', ')})`)
    } else {
      lines.push(`**Connected tools:** None`)
    }
    if (data.top_skills.length > 0) {
      lines.push(`**Top skills this week:** ${data.top_skills.join(', ')}`)
    } else {
      lines.push(`**Top skills this week:** None yet`)
    }
    lines.push(`**Agents created:** ${data.agents_created}`)
    lines.push(`**Skills installed:** ${data.agentfiles_count}`)
    return lines.join('\n')
  }

  if (loading) {
    return <div className="p-6 text-slate-400 text-sm">Loading...</div>
  }

  if (error || !data) {
    return (
      <div className="p-6 text-slate-400 text-sm">
        Couldn't load your setup right now. Try refreshing.
      </div>
    )
  }

  const shareText = buildShareText()

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">My AI setup</h1>
        <p className="text-slate-400 text-sm mt-1">
          A snapshot of how your AI assistant is set up and what you've been doing with it.
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4">
        <StatCard
          icon="smart_toy"
          label="Your AI assistant"
          value={data.provider}
          sub={`Model: ${data.model}`}
        />
        <StatCard
          icon="extension"
          label="Connected tools"
          value={data.connected_tools_count}
          sub={data.connected_tools.length > 0 ? data.connected_tools.join(', ') : 'None connected'}
        />
        <StatCard
          icon="trending_up"
          label="Agents created"
          value={data.agents_created}
          sub="Total across all time"
        />
        <StatCard
          icon="description"
          label="Skills installed"
          value={data.agentfiles_count}
          sub="Ready to run"
        />
      </div>

      {/* Top skills */}
      {data.top_skills.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Top skills this week</h2>
          <div className="flex flex-wrap gap-2">
            {data.top_skills.map((skill) => (
              <span
                key={skill}
                className="px-3 py-1.5 rounded-full bg-slate-700/60 text-slate-300 text-sm font-medium"
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div>
        <h2 className="text-sm font-semibold text-slate-300 mb-3">Export and share</h2>
        <div className="flex flex-wrap gap-3">
          <button
            data-testid="copy-markdown-btn"
            onClick={handleCopyMarkdown}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
          >
            <Icon name={copied ? 'check' : 'content_copy'} className="text-base" />
            {copied ? 'Copied!' : 'Copy as markdown'}
          </button>
          <button
            data-testid="download-config-btn"
            onClick={handleDownloadConfig}
            disabled={downloading}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Icon name="download" className="text-base" />
            {downloading ? 'Downloading...' : 'Download config'}
          </button>
        </div>
      </div>

      {/* Shareable text block */}
      <div>
        <h2 className="text-sm font-semibold text-slate-300 mb-3">Share anywhere</h2>
        <p className="text-xs text-slate-500 mb-2">Paste this into a message, doc, or anywhere else.</p>
        <pre
          data-testid="share-text-block"
          className="bg-slate-800/60 border border-slate-700/50 rounded-lg p-4 text-slate-300 text-xs whitespace-pre-wrap font-mono leading-relaxed overflow-x-auto"
        >
          {shareText}
        </pre>
      </div>
    </div>
  )
}
