import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from '../components/Icon'
import { OrgNameStep, finishTeamOnboarding } from '../components/TeamOnboardingSteps'
import { useAppStore } from '../stores/app'

export default function TeamStart() {
  const navigate = useNavigate()
  const setInstanceMode = useAppStore((s) => s.setInstanceMode)
  const [orgName, setOrgName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleStart() {
    if (!orgName.trim()) return
    setLoading(true)
    setError('')
    const ok = await finishTeamOnboarding({
      orgName: orgName.trim(),
      adminEmail: '',
      inviteEmails: [],
      isolationLevel: 'open',
    })
    if (ok) {
      setInstanceMode('team')
      navigate('/admin')
    } else {
      setError('Something went wrong. Please try again.')
      setLoading(false)
    }
  }

  return (
    <div className="max-w-lg mx-auto pt-16 px-6">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-indigo-500/20 flex items-center justify-center">
            <Icon name="groups" className="text-indigo-600 dark:text-indigo-400 text-xl" />
          </div>
          <h1 className="text-2xl font-bold">Share with your team</h1>
        </div>
        <p className="text-slate-600 dark:text-slate-400">
          Team mode lets your whole group share a workspace, run agents together, and see each other's work. You stay in control as the admin.
        </p>
      </div>

      <div className="mb-6">
        <OrgNameStep
          orgName={orgName}
          setOrgName={setOrgName}
          inputCls="bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white placeholder-slate-500"
          subtextCls="text-slate-600 dark:text-slate-400"
        />
      </div>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex gap-3">
        <button
          onClick={() => navigate(-1)}
          className="px-5 py-2.5 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors text-sm"
        >
          Cancel
        </button>
        <button
          data-testid="team-start-button"
          onClick={handleStart}
          disabled={!orgName.trim() || loading}
          className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium transition-colors text-sm"
        >
          {loading ? 'Setting up...' : 'Start my team'}
        </button>
      </div>
    </div>
  )
}
