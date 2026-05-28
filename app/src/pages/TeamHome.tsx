import { useState, useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import Icon from '../components/Icon'
import { api } from '../lib/api'
import { useAppStore } from '../stores/app'

interface CatalogEntry {
  id: string
  name: string
  version: number
  published_at: string
  signed_by_org: string
}

interface Announcement {
  id?: string
  title: string
  body?: string
  created_at?: string
}

interface AdoptionSummary {
  active_members_this_week: number
  top_skill: string | null
}

interface TeamHomeData {
  team_picks: CatalogEntry[]
  announcements: Announcement[]
  adoption_summary: AdoptionSummary
}

export default function TeamHome() {
  const instanceMode = useAppStore((s) => s.instanceMode)
  const darkMode = useAppStore((s) => s.darkMode)
  const [data, setData] = useState<TeamHomeData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<TeamHomeData>('/team/home')
      .then((res) => { setData(res); setError('') })
      .catch((err: unknown) => { setError(err instanceof Error ? err.message : 'Failed to load') })
      .finally(() => setLoading(false))
  }, [])

  if (instanceMode !== 'team') {
    return <Navigate to="/" replace />
  }

  const card = `rounded-xl border p-6 ${darkMode ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800' : 'bg-white border-slate-200'}`
  const sectionHeading = `text-base font-semibold ${darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'}`
  const empty = `text-sm ${darkMode ? 'text-slate-500' : 'text-slate-600 dark:text-slate-400'}`
  const divider = `border-b last:border-0 ${darkMode ? 'border-slate-200 dark:border-slate-800' : 'border-slate-100'}`
  const label = `text-xs uppercase tracking-wide font-medium ${darkMode ? 'text-slate-500' : 'text-slate-600 dark:text-slate-400'}`
  const primaryText = `font-medium ${darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'}`
  const secondaryText = `text-xs ${darkMode ? 'text-slate-500' : 'text-slate-600 dark:text-slate-400'}`

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16" data-testid="team-home-loading">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 text-red-600 dark:text-red-400 text-sm" data-testid="team-home-error">{error}</div>
    )
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6" data-testid="team-home">
      <h1 className={`text-2xl font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>Your Team</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Team Picks */}
        <div className={card} data-testid="team-picks-card">
          <div className="flex items-center gap-2 mb-4">
            <Icon name="star" className="text-yellow-600 dark:text-yellow-400" />
            <h2 className={sectionHeading}>Team picks</h2>
          </div>
          {data?.team_picks.length === 0 ? (
            <p className={empty} data-testid="team-picks-empty">No shared resources yet.</p>
          ) : (
            <ul className="space-y-1" data-testid="team-picks-list">
              {data?.team_picks.map((pick) => (
                <li key={pick.id} className={`flex items-start gap-3 py-2 ${divider}`} data-testid={`pick-${pick.id}`}>
                  <Icon name="description" className={`text-base mt-0.5 ${darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'}`} />
                  <div>
                    <p className={`text-sm ${primaryText}`}>{pick.name}</p>
                    <p className={secondaryText}>v{pick.version} · shared by {pick.signed_by_org}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Adoption summary */}
        <div className={card} data-testid="adoption-card">
          <div className="flex items-center gap-2 mb-4">
            <Icon name="trending_up" className="text-green-600 dark:text-green-400" />
            <h2 className={sectionHeading}>What the team is using</h2>
          </div>
          {data?.adoption_summary && (
            <div className="space-y-4">
              <div>
                <p className={`${label} mb-1`}>Active this week</p>
                <p className={`text-3xl font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`} data-testid="active-members-count">
                  {data.adoption_summary.active_members_this_week}
                </p>
                <p className={secondaryText}>team members</p>
              </div>
              {data.adoption_summary.top_skill && (
                <div>
                  <p className={`${label} mb-1`}>Most used resource</p>
                  <p className={`text-sm ${primaryText}`} data-testid="top-skill">{data.adoption_summary.top_skill}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Announcements */}
      <div className={card} data-testid="announcements-card">
        <div className="flex items-center gap-2 mb-4">
          <Icon name="campaign" className="text-indigo-600 dark:text-indigo-400" />
          <h2 className={sectionHeading}>Announcements</h2>
        </div>
        {data?.announcements.length === 0 ? (
          <p className={empty} data-testid="announcements-empty">No announcements yet.</p>
        ) : (
          <ul className="space-y-4" data-testid="announcements-list">
            {data?.announcements.map((a, i) => (
              <li key={a.id ?? i} className={`pb-4 ${divider}`} data-testid={`announcement-${a.id ?? i}`}>
                <p className={`text-sm ${primaryText}`}>{a.title}</p>
                {a.body && <p className={`text-sm mt-1 ${darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-600'}`}>{a.body}</p>}
                {a.created_at && (
                  <p className={`text-xs mt-1 ${darkMode ? 'text-slate-600' : 'text-slate-600 dark:text-slate-400'}`}>
                    {new Date(a.created_at).toLocaleDateString()}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
