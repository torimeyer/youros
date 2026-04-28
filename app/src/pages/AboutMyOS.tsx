import TopBar from '../components/TopBar'

interface Section {
  id: string
  heading: string
  body: React.ReactNode
}

export default function AboutMyOS() {
  const sections: Section[] = [
    {
      id: 'what-myos-is',
      heading: 'What myOS is',
      body: (
        <p className="text-sm text-slate-400">
          myOS is a workspace that runs locally on your computer. It helps you organize tasks and
          notes, and coordinates AI agents on your behalf. It is built around the idea that your
          work should compound, not vanish at the end of each session.
        </p>
      ),
    },
    {
      id: 'kernel-underneath',
      heading: 'The kernel underneath: ostk',
      body: (
        <p className="text-sm text-slate-400">
          myOS is the part you see. ostk is the coordination layer underneath. ostk tracks who
          changed what, prevents agents from stepping on each other, keeps a record of every
          action, and remembers context across sessions. Without it, agents would conflict and
          forget. With it, they cooperate and pick up where they left off.
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
          Tasks, notes, and decisions all live in a structured store. Past work is searchable and
          reusable. Closing a task today changes what is suggested next week.
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
            <a href="/privacy" className="underline hover:text-slate-200">
              Privacy
            </a>{' '}
            for a plain-language summary of how your data is handled.
          </li>
          <li>
            <a
              href="https://github.com/aetherwing-io"
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
    <div className="min-h-dvh bg-slate-950">
      <TopBar title="About" />
      <main className="pt-24 pb-16 px-8 max-w-2xl">
        <p className="text-slate-400 text-sm mb-8">
          What myOS is, how it works, and where to learn more.
        </p>

        <div className="space-y-6">
          {sections.map((section) => (
            <div
              key={section.id}
              data-testid={`about-section-${section.id}`}
              className="bg-slate-900 border border-slate-800 rounded-xl p-6"
            >
              <h2
                className="text-base font-semibold text-slate-100 mb-4"
                data-testid={`about-heading-${section.id}`}
              >
                {section.heading}
              </h2>
              {section.body}
            </div>
          ))}
        </div>

        <p className="text-xs text-slate-600 mt-10">Updated: April 2026</p>
      </main>
    </div>
  )
}
