import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

interface MemoryCountResponse {
  bullet_count: number
  file_exists: boolean
}

export default function MemoryPill() {
  const navigate = useNavigate()
  const [count, setCount] = useState<number | null>(null)

  useEffect(() => {
    api.get<MemoryCountResponse>('/memory/count')
      .then((d) => setCount(d.bullet_count))
      .catch(() => setCount(null))
  }, [])

  if (count === null || count === 0) return null

  return (
    <button
      onClick={() => navigate('/settings#memory')}
      title="Things I remember about you. Click to edit."
      data-testid="memory-pill"
      className="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700 text-xs text-slate-300 hover:text-white hover:border-slate-500 transition-colors cursor-pointer"
    >
      🧠 {count} remembered
    </button>
  )
}
