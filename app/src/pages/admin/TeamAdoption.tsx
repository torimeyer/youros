import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface RollupBuckets {
  power_user: string[]
  active: string[]
  explorer: string[]
  inactive: string[]
}

interface RollupData {
  buckets: RollupBuckets
  top_skill: string
  adoption_gap: string
}

const BUCKET_CONFIG = [
  {
    key: 'power_user' as const,
    label: 'Power users',
    description: 'Using AI heavily and deeply every week',
    icon: 'bolt',
    color: 'text-yellow-500',
    bg: 'bg-yellow-500/10',
    border: 'border-yellow-500/20',
  },
  {
    key: 'active' as const,
    label: 'Active',
    description: 'Frequent use, lighter tasks',
    icon: 'trending_up',
    color: 'text-green-500',
    bg: 'bg-green-500/10',
    border: 'border-green-500/20',
  },
  {
    key: 'explorer' as const,
    label: 'Exploring',
    description: 'Occasional use, high-impact sessions',
    icon: 'explore',
    color: 'text-blue-500',
    bg: 'bg-blue-500/10',
    border: 'border-blue-500/20',
  },
  {
    key: 'inactive' as const,
    label: 'Inactive',
    description: 'No activity in the last 7 days',
    icon: 'person_off',
    color: 'text-slate-600 dark:text-slate-400',
    bg: 'bg-slate-500/10',
    border: 'border-slate-500/20',
  },
]

export default function TeamAdoption() {
  const darkMode = useAppStore((s) => s.darkMode)
  const [data, setData] = useState<RollupData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<RollupData>('/team/adoption-rollup')
      .then((res) => { setData(res); setError('') })
      .catch((err: unknown) => { setError(err instanceof Error ? err.message : 'Failed to load') })
      .finally(() => setLoading(false))
  }, [])

  const cardCls = `rounded-xl border p-5 ${darkMode ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-600 dark:text-red-400 text-sm">
        {error}
      </div>
    )
  }

  if (!data) return null

  const totalMembers = Object.values(data.buckets).flat().length

  return (
    <div data-testid="team-adoption-page" className="space-y-6">
      <div>
        <h1 className={`text-xl font-bold mb-1 ${valueCls}`}>Team adoption</h1>
        <p className={`text-sm ${labelCls}`}>
          How your team is using AI this week — based on the last 7 days of activity.
        </p>
      </div>

      {/* Callouts */}
      {(data.top_skill || data.adoption_gap) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {data.top_skill && (
            <div
              data-testid="top-skill-callout"
              className={`${cardCls} flex items-start gap-3`}
            >
              <Icon name="star" className="text-yellow-500 mt-0.5" size={20} />
              <div>
                <p className={`text-xs uppercase font-semibold mb-0.5 ${labelCls}`}>Top skill this week</p>
                <p className={`font-semibold ${valueCls}`}>{data.top_skill}</p>
              </div>
            </div>
          )}
          {data.adoption_gap && (
            <div
              data-testid="adoption-gap-callout"
              className={`${cardCls} flex items-start gap-3`}
            >
              <Icon name="warning_amber" className="text-orange-600 dark:text-orange-400 mt-0.5" size={20} />
              <div>
                <p className={`text-xs uppercase font-semibold mb-0.5 ${labelCls}`}>Adoption gap</p>
                <p className={`font-semibold ${valueCls}`}>
                  {data.adoption_gap}
                </p>
                <p className={`text-xs mt-0.5 ${labelCls}`}>
                  Nobody has used this skill yet
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Bucket cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {BUCKET_CONFIG.map((bucket) => {
          const members = data.buckets[bucket.key] ?? []
          return (
            <div
              key={bucket.key}
              data-testid={`bucket-${bucket.key}`}
              className={`${cardCls}`}
            >
              <div className="flex items-center gap-2 mb-3">
                <span className={`p-1.5 rounded-lg ${bucket.bg} ${bucket.border} border`}>
                  <Icon name={bucket.icon} className={bucket.color} size={18} />
                </span>
                <div>
                  <p className={`font-semibold text-sm ${headingCls}`}>{bucket.label}</p>
                  <p className={`text-xs ${labelCls}`}>{bucket.description}</p>
                </div>
                <span
                  data-testid={`bucket-count-${bucket.key}`}
                  className={`ml-auto text-2xl font-bold ${valueCls}`}
                >
                  {members.length}
                </span>
              </div>

              {members.length > 0 ? (
                <div className="flex flex-wrap gap-1.5" data-testid={`bucket-members-${bucket.key}`}>
                  {members.map((email) => (
                    <span
                      key={email}
                      className={`text-xs px-2 py-0.5 rounded-full ${darkMode ? 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300' : 'bg-slate-100 text-slate-600'}`}
                    >
                      {email}
                    </span>
                  ))}
                </div>
              ) : (
                <p className={`text-xs ${labelCls}`} data-testid={`bucket-empty-${bucket.key}`}>
                  No one here yet
                </p>
              )}
            </div>
          )
        })}
      </div>

      {totalMembers === 0 && (
        <div className={`${cardCls} text-center py-8`}>
          <Icon name="groups" className={`text-4xl mb-2 ${labelCls}`} />
          <p className={`text-sm ${labelCls}`}>No usage data yet. Activity will show up here once your team starts working.</p>
        </div>
      )}
    </div>
  )
}
