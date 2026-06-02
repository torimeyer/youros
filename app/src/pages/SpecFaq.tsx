import PageShell from '../components/PageShell'

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
      <div className="text-sm text-slate-400 space-y-3">
        <p>
          A spec describes WHAT you want to build and WHY it matters. It is the shared source of
          truth for everyone working on a piece of work, whether that is you, a teammate, or an
          agent. A spec does not tell anyone HOW to do the work. That part is up to the builder.
        </p>
        <p>
          yourOS is spec-first: important work starts with a spec. It is also spec-anchored: specs
          are kept and updated over time as the work progresses. This is not a system where you can
          only edit the spec and never the work. Specs serve the work, not the other way around.
        </p>
        <p>
          You can change this in Settings: choose whether a spec always turns into a plan to build,
          or stays a document you act on when you are ready.
        </p>
      </div>
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
    id: 'faq-q-vs-prd',
    heading: 'How is a spec different from a PRD?',
    body: (
      <div className="text-sm text-slate-400 space-y-3">
        <p>
          A spec is a PRD for agents. A PRD is written for people, who fill the gaps from context
          and hallway conversations. An agent does exactly what is on the page, so the writing has
          to be different.
        </p>
        <p>
          The acceptance criteria have to be concrete and checkable, not vague words like premium or
          clean. The constraints and non-goals have to be written down, not assumed. And the spec
          has to stand on its own, because the agent was not in the room.
        </p>
        <p>
          A spec is also executable: each criterion spawns a builder, the spec marks itself done
          when they all land, and work cannot start until it is marked Ready.
        </p>
        <p>
          So a PRD is a spec for humans. A spec is a PRD for agents, written precisely enough that
          something non-human can build straight from it.
        </p>
      </div>
    ),
  },
  {
    id: 'faq-q-kinds',
    heading: "Specs aren't just for engineers",
    body: (
      <div className="text-sm text-slate-400 space-y-2">
        <p>
          The same precise, build-from-it document can produce very different things, for different
          audiences:
        </p>
        <ul className="space-y-2 list-disc list-inside">
          <li>Prototype: a quick, throwaway build to test an idea before committing.</li>
          <li>
            Vision / Roadmap: where the product is headed over time, like a deployed interactive
            vision doc.
          </li>
          <li>Engineering feature: a specific capability to build.</li>
          <li>
            Landing page or launch: a marketing page with the sections, message, and call to action
            spelled out.
          </li>
          <li>Dashboard or report: a data view with the exact metrics, filters, and refresh cadence.</li>
          <li>Customer docs: a tutorial or reference written for the people using the product.</li>
        </ul>
      </div>
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
]

export default function SpecFaq() {
  return (
    <PageShell title="Spec FAQ">
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
    </PageShell>
  )
}
