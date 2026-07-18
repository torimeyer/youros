import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import Icon from '../components/Icon'
import PageShell from '../components/PageShell'

interface Skill {
  id: string
  name: string
  uses_this_week: number
  prev_week_uses: number
}

function skillDelta(skill: Skill): { label: string; color: string } | null {
  if (skill.prev_week_uses === 0 && skill.uses_this_week > 0) {
    return { label: 'new', color: 'text-slate-600 dark:text-slate-400' }
  }
  if (skill.prev_week_uses > 0) {
    const pct = Math.round(((skill.uses_this_week - skill.prev_week_uses) / skill.prev_week_uses) * 100)
    return {
      label: pct >= 0 ? `+${pct}%` : `${pct}%`,
      color: pct > 0 ? 'text-green-600 dark:text-green-400' : pct < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-600 dark:text-slate-400',
    }
  }
  return null
}

function truncatePriority(text: string): string {
  const colonIdx = text.indexOf(':')
  if (colonIdx > 0 && colonIdx <= 80) return text.slice(0, colonIdx)
  if (text.length <= 80) return text
  return text.slice(0, 80) + '…'
}

interface Recommendation {
  id: string
  name: string
  why: string
}

interface ThisWeek {
  agent_runs_completed: number
  top_spec_or_task: string | null
}

interface AdoptionData {
  top_skills: Skill[]
  recommendations: Recommendation[]
  this_week: ThisWeek
}

export default function Adoption() {
  const navigate = useNavigate()
  const [data, setData] = useState<AdoptionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    api.get<AdoptionData>('/adoption/whats-working')
      .then((d) => { setData(d); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  if (loading || error || !data) {
    return (
      <PageShell title="What's working">
        <div className="max-w-2xl mx-auto">
          <p className="text-slate-600 dark:text-slate-400 text-sm py-8">
            {loading ? 'Loading...' : "Couldn't load your activity right now. Try refreshing."}
          </p>
        </div>
      </PageShell>
    )
  }

  const hasActivity = data.top_skills.length > 0

  return (
    <PageShell title="What's working">
      <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">What's working</h1>
        <p className="text-slate-600 dark:text-slate-400 text-sm mt-1">
          A quick look at what you've been running this week and what to try next.
        </p>
      </div>

      {/* Top skills */}
      <section aria-labelledby="top-skills-heading">
        <h2 id="top-skills-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
          What you used this week
        </h2>

        {hasActivity ? (
          <div className="space-y-2">
            {data.top_skills.map((skill) => (
              <div
                key={skill.id}
                data-testid="skill-card"
                className="flex items-center justify-between bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <Icon name="bolt" className="text-amber-600 dark:text-amber-400 text-lg" />
                  <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{skill.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  {(() => {
                    const delta = skillDelta(skill)
                    return delta ? (
                      <span
                        data-testid="skill-delta"
                        className={`text-xs ${delta.color} bg-slate-200 dark:bg-slate-700/50 px-2 py-0.5 rounded-full`}
                      >
                        {delta.label}
                      </span>
                    ) : null
                  })()}
                  <span
                    data-testid="skill-use-count"
                    className="text-xs text-slate-600 dark:text-slate-400 bg-slate-200 dark:bg-slate-700/50 px-2 py-0.5 rounded-full"
                  >
                    {skill.uses_this_week}x this week
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div
            data-testid="empty-state"
            className="space-y-2"
          >
            {[
              { id: 'builtin-builder', name: 'Builder', icon: 'engineering', desc: 'Build something new' },
              { id: 'builtin-brainstorm', name: 'Brainstorm', icon: 'lightbulb', desc: 'Explore ideas and approaches' },
              { id: 'builtin-research', name: 'Research', icon: 'search', desc: 'Look into a topic or question' },
            ].map((card) => (
              <button
                key={card.id}
                type="button"
                data-testid="starter-card"
                aria-label={`Start a ${card.name} agent`}
                onClick={() => navigate(`/agents?template=${card.id}`)}
                className="w-full text-left flex items-start gap-3 bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-3 hover:bg-slate-50/60 dark:hover:bg-slate-800/60 hover:border-slate-600/60 transition-colors cursor-pointer"
              >
                <Icon name={card.icon} className="text-sky-400 text-lg mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{card.name}</p>
                  <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">{card.desc}</p>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Recommendations */}
      {data.recommendations.length > 0 && (
        <section aria-labelledby="recs-heading">
          <h2 id="recs-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
            What to try next
          </h2>
          <div className="space-y-2">
            {data.recommendations.map((rec) => (
              <button
                key={rec.id}
                type="button"
                data-testid="recommendation-card"
                aria-label={`Start a ${rec.name} agent`}
                onClick={() => navigate(`/agents?template=${rec.id}`)}
                className="w-full text-left flex items-start gap-3 bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-3 hover:bg-slate-50/60 dark:hover:bg-slate-800/60 hover:border-slate-600/60 transition-colors cursor-pointer"
              >
                <Icon name="lightbulb" className="text-sky-400 text-lg mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{rec.name}</p>
                  <p data-testid="rec-why" className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">
                    Because {rec.why}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* This week summary */}
      <section aria-labelledby="week-heading">
        <h2 id="week-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
          This week
        </h2>
        <div className="bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-4 space-y-3">
          <div className="flex items-center gap-3">
            <Icon name="check_circle" className="text-green-600 dark:text-green-400 text-lg shrink-0" />
            <span className="text-sm text-slate-700 dark:text-slate-300">
              {data.this_week.agent_runs_completed === 0
                ? 'No agent runs finished this week yet.'
                : `${data.this_week.agent_runs_completed} agent run${data.this_week.agent_runs_completed === 1 ? '' : 's'} finished`}
            </span>
          </div>
          {data.this_week.top_spec_or_task && (
            <div className="flex items-center gap-3">
              <Icon name="flag" className="text-amber-600 dark:text-amber-400 text-lg shrink-0" />
              <span className="text-sm text-slate-700 dark:text-slate-300" data-testid="top-priority-label">
                Top priority: {truncatePriority(data.this_week.top_spec_or_task!)}
              </span>
            </div>
          )}
        </div>
      </section>
    </div>
    </PageShell>
  )
}
