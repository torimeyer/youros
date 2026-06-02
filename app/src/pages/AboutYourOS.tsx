import { useEffect, useState } from 'react'
import PageShell from '../components/PageShell'
import { api } from '../lib/api'

interface Section {
  id: string
  heading: string
  body: React.ReactNode
}

export default function AboutYourOS() {
  const [version, setVersion] = useState('')
  const [taskCount, setTaskCount] = useState<number | null>(null)
  const [agentCount, setAgentCount] = useState<number | null>(null)
  const [decisionCount, setDecisionCount] = useState<number | null>(null)

  useEffect(() => {
    api.get<{ myos: { current: string } }>('/upgrade/status')
      .then((res) => setVersion(res.myos?.current ?? ''))
      .catch(() => {})
    api.get<{ tasks: unknown[] }>('/tasks')
      .then((res) => setTaskCount(Array.isArray(res.tasks) ? res.tasks.length : 0))
      .catch(() => {})
    api.get<{ agents: unknown[] }>('/agents')
      .then((res) => setAgentCount(Array.isArray(res.agents) ? res.agents.length : 0))
      .catch(() => {})
    api.get<{ count: number }>('/decisions')
      .then((res) => setDecisionCount(typeof res.count === 'number' ? res.count : 0))
      .catch(() => {})
  }, [])

  const totalCounts = (taskCount ?? 0) + (agentCount ?? 0) + (decisionCount ?? 0)
  const showCounters = totalCounts > 0

  const sections: Section[] = [
    {
      id: 'what-myos-is',
      heading: 'What yourOS is',
      body: (
        <p className="text-sm text-slate-400">
          yourOS is a workspace that runs locally on your computer. It helps you organize needles and
          notes, and coordinates AI agents on your behalf. It is built around the idea that your
          work should compound, not vanish at the end of each session.
        </p>
      ),
    },
    {
      id: 'why-not-wrapper',
      heading: 'Why this is not another AI wrapper',
      body: (
        <p className="text-sm text-slate-400">
          Most AI tools call a model and forget. yourOS coordinates. The kernel underneath tracks
          every file change, every agent run, every decision, so that next week's work picks up
          where today's left off. The AI provider is interchangeable. The coordination is not.
        </p>
      ),
    },
    {
      id: 'kernel-underneath',
      heading: 'The kernel underneath: ostk',
      body: (
        <p className="text-sm text-slate-400">
          yourOS is the part you see. Underneath, it runs on ostk, an independent open-source
          coordination kernel that yourOS is built on top of. ostk tracks who changed what, prevents
          agents from stepping on each other, keeps a record of every action, and remembers
          context across sessions. Without it, agents would conflict and forget. With it, they
          cooperate and pick up where they left off.
        </p>
      ),
    },
    {
      id: 'how-agents-coordinate',
      heading: 'How agents coordinate',
      body: (
        <p className="text-sm text-slate-400">
          When you spawn an agent, ostk gives it an identity, an isolated workspace, and a record
          of the project state. Multiple agents can run at the same time without overwriting each
          other's work because the kernel tracks file generations and detects conflicts before they
          land.
        </p>
      ),
    },
    {
      id: 'work-that-compounds',
      heading: 'Work that compounds',
      body: (
        <p className="text-sm text-slate-400">
          Needles, notes, and decisions all live in a structured store. Past work is searchable and
          reusable. Closing a needle today changes what is suggested next week.
        </p>
      ),
    },
    {
      id: 'local-and-model-agnostic',
      heading: 'Local first, model agnostic',
      body: (
        <p className="text-sm text-slate-400">
          Your data stays on your machine in{' '}
          <code className="text-slate-300 bg-slate-800 px-1 rounded">~/.myos/</code>. The AI
          provider is a setting, not a dependency. Anthropic, Google, and others are pluggable.
          Switch providers without losing your work.
        </p>
      ),
    },
    {
      id: 'where-to-look-next',
      heading: 'Where to look next',
      body: (
        <ul className="text-sm text-slate-400 space-y-2 list-disc list-inside">
          <li>
            <a href="/settings" className="underline hover:text-slate-200">
              Settings
            </a>{' '}
            to configure your AI provider, standing instructions, and connected services.
          </li>
          <li>
            <a href="/settings#shortcuts" className="underline hover:text-slate-200">
              Keyboard shortcuts
            </a>{' '}
            for the moves that save the most time.
          </li>
          <li>
            <a href="/privacy" className="underline hover:text-slate-200">
              Privacy
            </a>{' '}
            for a plain-language summary of how your data is handled.
          </li>
          <li>
            <a
              href="https://github.com/torimeyer/myos"
              className="underline hover:text-slate-200"
              target="_blank"
              rel="noopener noreferrer"
            >
              Project repository
            </a>{' '}
            for the open-source code.
          </li>
        </ul>
      ),
    },
  ]

  return (
    <PageShell title="About">
      <main className="pt-24 pb-16 px-8 max-w-3xl mx-auto">
        <div data-testid="about-hero" className="mb-10">
          <h1 className="text-4xl font-bold text-slate-100 mb-1">yourOS</h1>
          <p className="text-[13px] italic text-slate-500 mb-3">an operating system for how you operate. this is your os.</p>
          <p className="text-lg text-slate-400">
            A workspace that remembers your work and coordinates AI agents on your behalf, all
            running locally on your machine.
          </p>
        </div>

        {showCounters && (
          <div
            data-testid="about-counters"
            className="grid grid-cols-3 gap-4 mb-10 bg-slate-900 border border-slate-800 rounded-xl p-6"
          >
            {(taskCount ?? 0) > 0 && (
              <div className="text-center">
                <div className="text-3xl font-semibold text-slate-100">{taskCount}</div>
                <div className="text-xs text-slate-500 mt-1">needles tracked</div>
              </div>
            )}
            {(agentCount ?? 0) > 0 && (
              <div className="text-center">
                <div className="text-3xl font-semibold text-slate-100">{agentCount}</div>
                <div className="text-xs text-slate-500 mt-1">agents run</div>
              </div>
            )}
            {(decisionCount ?? 0) > 0 && (
              <div className="text-center">
                <div className="text-3xl font-semibold text-slate-100">{decisionCount}</div>
                <div className="text-xs text-slate-500 mt-1">decisions logged</div>
              </div>
            )}
          </div>
        )}

        <div className="space-y-6">
          {sections.map((section) => (
            <div
              key={section.id}
              data-testid={`about-section-${section.id}`}
              className="bg-slate-900 border border-slate-800 rounded-xl p-6"
            >
              <h2
                className="text-lg font-semibold text-slate-100 mb-4"
                data-testid={`about-heading-${section.id}`}
              >
                {section.heading}
              </h2>
              {section.body}
            </div>
          ))}
        </div>

        {version && (
          <p
            data-testid="about-version"
            className="text-xs text-slate-600 mt-10 text-center font-mono"
          >
            yourOS {version}
          </p>
        )}
      </main>
    </PageShell>
  )
}
