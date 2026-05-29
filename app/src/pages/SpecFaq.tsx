import TopBar from '../components/TopBar'

interface FaqSection {
  id: string
  heading: string
  body: React.ReactNode
}

const sections: FaqSection[] = [
  {
    id: 'faq-q-what-is-a-spec',
    heading: 'What is a spec?',
    body: (
      <p className="text-sm text-slate-400">
        A spec describes WHAT you want to build and WHY it matters. It is the shared source of truth
        for everyone working on a piece of work, whether that is you, a teammate, or an agent. A
        spec does not tell anyone HOW to do the work. That part is up to the builder.
      </p>
    ),
  },
  {
    id: 'faq-q-what-its-not',
    heading: 'What a spec is NOT',
    body: (
      <ul className="text-sm text-slate-400 space-y-2 list-disc list-inside">
        <li>Not code. A spec describes intent, not implementation.</li>
        <li>Not a step-by-step plan. Plans come after the spec, not before.</li>
        <li>
          Not just a long prompt. A prompt is a one-time input. A spec is a living document that
          gets updated as the work evolves.
        </li>
      </ul>
    ),
  },
  {
    id: 'faq-q-kinds',
    heading: 'The kinds of specs',
    body: (
      <ul className="text-sm text-slate-400 space-y-2 list-disc list-inside">
        <li>Prototype: an early idea, not yet ready to build from.</li>
        <li>Vision / Roadmap: a direction for where the product is going over time.</li>
        <li>Customer docs: material written for people using the product.</li>
        <li>Engineering feature: a description of a specific capability to build.</li>
      </ul>
    ),
  },
  {
    id: 'faq-q-statuses',
    heading: 'What the statuses mean',
    body: (
      <ul className="text-sm text-slate-400 space-y-2 list-disc list-inside">
        <li>Draft: still being written. Not ready to act on.</li>
        <li>Ready: complete enough to start work from.</li>
        <li>In Progress: work is actively happening against this spec.</li>
        <li>Done: the work described in the spec is complete.</li>
      </ul>
    ),
  },
  {
    id: 'faq-q-required-optional',
    heading: 'Required vs optional',
    body: (
      <p className="text-sm text-slate-400">
        Required fields must be filled before a spec can move to Ready. Optional fields are not
        needed to start work, but they strengthen the spec by adding context, constraints, or
        examples.
      </p>
    ),
  },
  {
    id: 'faq-q-when-not',
    heading: 'When you do NOT need a spec',
    body: (
      <p className="text-sm text-slate-400">
        You do not need a spec for a tiny fix, a quick refactor, or a one-off task that takes less
        than an hour and will not be revisited.
      </p>
    ),
  },
  {
    id: 'faq-q-how-treated',
    heading: 'How yourOS treats specs',
    body: (
      <p className="text-sm text-slate-400">
        yourOS is spec-first: important work starts with a spec. It is also spec-anchored: specs are
        kept and updated over time as the work progresses. This is not a system where you can only
        edit the spec and never the work. Specs serve the work, not the other way around.
      </p>
    ),
  },
]

export default function SpecFaq() {
  return (
    <div className="min-h-dvh bg-slate-950">
      <TopBar title="Spec FAQ" />
      <main className="pt-24 pb-16 px-8 max-w-3xl mx-auto">
        <div className="mb-10">
          <h1 className="text-4xl font-bold text-slate-100 mb-3">Specs: the basics</h1>
          <p className="text-lg text-slate-400">
            Plain answers to the most common questions about specs in yourOS.
          </p>
        </div>

        <div className="space-y-6">
          {sections.map((section) => (
            <div
              key={section.id}
              data-testid={section.id}
              className="bg-slate-900 border border-slate-800 rounded-xl p-6"
            >
              <h2 className="text-lg font-semibold text-slate-100 mb-4">{section.heading}</h2>
              {section.body}
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
