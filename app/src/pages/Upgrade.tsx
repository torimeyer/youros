import { useState, useEffect } from 'react'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import { api } from '../lib/api'

interface VersionInfo {
  current: string
  latest: string
  behind: boolean
  commits_behind?: number
}

interface UpgradeStatus {
  myos: VersionInfo
  ostk: VersionInfo
}

interface UpgradeResult {
  success: boolean
  message: string
}

type UpgradeTarget = 'myos' | 'ostk' | 'both'

export default function Upgrade() {
  const [status, setStatus] = useState<UpgradeStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState<UpgradeTarget | null>(null)
  const [result, setResult] = useState<UpgradeResult | null>(null)

  const fetchStatus = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get<UpgradeStatus>('/upgrade/status')
      setStatus(data)
    } catch (e) {
      setError('Could not check for updates. Make sure you are connected to the internet.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  const runUpgrade = async (target: UpgradeTarget) => {
    setRunning(target)
    setResult(null)
    try {
      const data = await api.post<UpgradeResult>('/upgrade/run', { target })
      setResult(data)
      // Refresh status after upgrade
      await fetchStatus()
    } catch (e) {
      setResult({ success: false, message: 'Something went wrong. Please try again.' })
    } finally {
      setRunning(null)
    }
  }

  const bothBehind = status?.myos.behind && status?.ostk.behind

  return (
    <div className="min-h-dvh bg-slate-950">
      <TopBar title="Updates" />
      <main className="pt-24 pb-12 px-8 max-w-2xl">
        <p className="text-slate-400 mb-8 text-sm">
          Keep everything up to date to get the latest features and fixes.
        </p>

        {loading && (
          <div className="flex items-center gap-3 text-slate-400">
            <div className="w-4 h-4 border-2 border-slate-600 border-t-blue-400 rounded-full animate-spin" />
            <span className="text-sm">Checking for updates...</span>
          </div>
        )}

        {error && (
          <div className="flex items-center gap-3 text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 text-sm">
            <Icon name="error_outline" size={18} className="shrink-0" />
            {error}
          </div>
        )}

        {!loading && !error && status && (
          <div className="flex flex-col gap-4">
            <ComponentCard
              name="myOS"
              description="Your personal operating system"
              info={status.myos}
              onUpdate={() => runUpgrade('myos')}
              updating={running === 'myos' || running === 'both'}
            />
            <ComponentCard
              name="System Engine"
              description="The task and memory engine powering myOS"
              info={status.ostk}
              onUpdate={() => runUpgrade('ostk')}
              updating={running === 'ostk' || running === 'both'}
            />

            {bothBehind && !running && (
              <button
                onClick={() => runUpgrade('both')}
                className="mt-2 w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors"
              >
                Update both
              </button>
            )}

            {result && (
              <div
                className={`flex items-start gap-3 rounded-xl px-4 py-3 text-sm border ${
                  result.success
                    ? 'bg-green-500/10 border-green-500/20 text-green-300'
                    : 'bg-red-500/10 border-red-500/20 text-red-300'
                }`}
              >
                <Icon
                  name={result.success ? 'check_circle' : 'error_outline'}
                  size={18}
                  className="shrink-0 mt-0.5"
                />
                <span>{result.message}</span>
              </div>
            )}

            <button
              onClick={fetchStatus}
              className="mt-2 text-xs text-slate-600 hover:text-slate-400 transition-colors self-start"
            >
              Refresh status
            </button>
          </div>
        )}
      </main>
    </div>
  )
}

function ComponentCard({
  name,
  description,
  info,
  onUpdate,
  updating,
}: {
  name: string
  description: string
  info: VersionInfo
  onUpdate: () => void
  updating: boolean
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-semibold text-white text-sm">{name}</span>
            {info.behind ? (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-orange-500/20 text-orange-400">
                Update available
              </span>
            ) : (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400">
                Up to date
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mb-3">{description}</p>
          <div className="flex items-center gap-4 text-xs">
            <span className="text-slate-400">
              Installed: <span className="text-slate-300 font-mono">{info.current}</span>
            </span>
            {info.behind && (
              <span className="text-slate-400">
                Latest: <span className="text-blue-400 font-mono">{info.latest}</span>
              </span>
            )}
          </div>
          {info.behind && info.commits_behind != null && info.commits_behind > 0 && (
            <p className="text-xs text-slate-500 mt-1">
              {info.commits_behind} new {info.commits_behind === 1 ? 'commit' : 'commits'} available
            </p>
          )}
        </div>

        {info.behind && (
          <button
            onClick={onUpdate}
            disabled={updating}
            className="shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-semibold transition-colors"
          >
            {updating ? (
              <>
                <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Updating...
              </>
            ) : (
              <>
                <Icon name="system_update_alt" size={14} />
                Update
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )
}
