import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card } from './ui'
import Icon from './Icon'
import { api } from '../lib/api'

interface Counts {
  open: number
  p0: number
  p1: number
}

interface DashboardCounts {
  counts: Counts
}

export default function NeedlesCountWidget() {
  const navigate = useNavigate()
  const [counts, setCounts] = useState<Counts | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    api.get<DashboardCounts>('/dashboard')
      .then((data) => setCounts(data.counts))
      .catch(() => setError(true))
  }, [])

  return (
    <Card hover padding="sm" className="sm:p-6 cursor-pointer" onClick={() => navigate('/backlog')}>
      <div className="flex items-center gap-2 mb-4">
        <Icon name="pending_actions" className="text-orange-400" size={20} />
        <h2 className="text-lg font-semibold">Open needles</h2>
      </div>
      {error ? (
        <p className="text-sm text-slate-500" data-testid="needles-error">Could not load needle count.</p>
      ) : counts === null ? (
        <p className="text-sm text-slate-500" data-testid="needles-loading">Loading...</p>
      ) : (
        <div className="flex items-end gap-6" data-testid="needles-counts">
          <div>
            <p className="text-3xl font-bold text-white" data-testid="needles-open">{counts.open}</p>
            <p className="text-xs text-slate-400 mt-1">total open</p>
          </div>
          <div className="flex gap-4 pb-1">
            <div className="text-center">
              <p className="text-lg font-semibold text-red-400" data-testid="needles-p0">{counts.p0}</p>
              <p className="text-xs text-slate-500">P0</p>
            </div>
            <div className="text-center">
              <p className="text-lg font-semibold text-orange-400" data-testid="needles-p1">{counts.p1}</p>
              <p className="text-xs text-slate-500">P1</p>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
