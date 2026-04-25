import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import Icon from '../components/Icon'

interface Skill {
  id: string
  name: string
  uses_this_week: number
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
  const [data, setData] = useState<AdoptionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    api.get<AdoptionData>('/adoption/whats-working')
      .then((d) => { setData(d); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  if (loading) {
    return (
      <div className="p-6 text-slate-400 text-sm">Loading...</div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-6 text-slate-400 text-sm">
        Couldn't load your activity right now. Try refreshing.
      </div>
    )
  }

  const hasActivity = data.top_skills.length > 0

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">What's working</h1>
        <p className="text-slate-400 text-sm mt-1">
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
                className="flex items-center justify-between bg-slate-800/50 border border-slate-700/50 rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <Icon name="bolt" className="text-amber-400 text-lg" />
                  <span className="text-sm font-medium text-slate-100">{skill.name}</span>
                </div>
                <span
                  data-testid="skill-use-count"
                  className="text-xs text-slate-400 bg-slate-700/50 px-2 py-0.5 rounded-full"
                >
                  {skill.uses_this_week}x this week
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div
            data-testid="empty-state"
            className="bg-slate-800/30 border border-slate-700/40 rounded-lg px-4 py-5 text-center"
          >
            <p className="text-sm text-slate-400">No activity yet this week.</p>
            <p className="text-xs text-slate-500 mt-1">
              Try a skill from the Agents page to get started.
            </p>
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
              <div
                key={rec.id}
                data-testid="recommendation-card"
                className="flex items-start gap-3 bg-slate-800/30 border border-slate-700/40 rounded-lg px-4 py-3"
              >
                <Icon name="lightbulb" className="text-sky-400 text-lg mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-100">{rec.name}</p>
                  <p data-testid="rec-why" className="text-xs text-slate-400 mt-0.5">
                    Because {rec.why}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* This week summary */}
      <section aria-labelledby="week-heading">
        <h2 id="week-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
          This week
        </h2>
        <div className="bg-slate-800/30 border border-slate-700/40 rounded-lg px-4 py-4 space-y-3">
          <div className="flex items-center gap-3">
            <Icon name="check_circle" className="text-green-400 text-lg shrink-0" />
            <span className="text-sm text-slate-300">
              {data.this_week.agent_runs_completed === 0
                ? 'No agent runs finished this week yet.'
                : `${data.this_week.agent_runs_completed} agent run${data.this_week.agent_runs_completed === 1 ? '' : 's'} finished`}
            </span>
          </div>
          {data.this_week.top_spec_or_task && (
            <div className="flex items-center gap-3">
              <Icon name="flag" className="text-amber-400 text-lg shrink-0" />
              <span className="text-sm text-slate-300">
                Top priority: {data.this_week.top_spec_or_task}
              </span>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
