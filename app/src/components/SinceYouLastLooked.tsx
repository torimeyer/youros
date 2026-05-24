import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'

interface Agent {
  name: string
  status: string
  completed_at?: string
}

export function computeFinishedCount(agents: Agent[], lastViewed: string | null | undefined): number {
  if (!lastViewed) return 0
  return agents.filter((a) => a.completed_at && a.completed_at > lastViewed).length
}

export default function SinceYouLastLooked() {
  const agentsLastViewed = useAppStore((s) => s.agentsLastViewed)
  const navigate = useNavigate()
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!agentsLastViewed) return

    const fetchCompleted = async () => {
      try {
        const res = await api.get<{ agents: Agent[] }>('/agents?limit=50&status=completed')
        setCount(computeFinishedCount(res.agents || [], agentsLastViewed))
      } catch {
        // ignore
      }
    }

    fetchCompleted()
    const interval = setInterval(fetchCompleted, 30000)
    return () => clearInterval(interval)
  }, [agentsLastViewed])

  const handleClick = useCallback(() => {
    navigate('/agents')
  }, [navigate])

  if (count === 0) return null

  return (
    <button
      onClick={handleClick}
      className="group relative flex items-center gap-3 w-full px-4 py-2.5 rounded-lg transition-colors duration-200 cursor-pointer text-slate-400 hover:text-slate-100 hover:bg-slate-800/50"
      title="Agents finished while you were away"
      data-testid="since-you-last-looked-button"
    >
      <Icon name="check_circle" className="text-xl" />
      <span className="text-sm font-medium">Finished</span>
      <span
        className="ml-auto min-w-[16px] h-4 bg-blue-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center px-1"
        data-testid="since-you-last-looked-badge"
      >
        {count > 9 ? '9+' : count}
      </span>
    </button>
  )
}
