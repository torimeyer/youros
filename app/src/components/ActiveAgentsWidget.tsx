import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card } from './ui'
import Icon from './Icon'
import { api } from '../lib/api'
import { type AgentInfo, isAgentActive, isUserSpawnedAgent } from '../lib/agentUtils'

interface AgentsResponse {
  agents: AgentInfo[]
}

export default function ActiveAgentsWidget() {
  const navigate = useNavigate()
  const [agents, setAgents] = useState<AgentInfo[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    api.get<AgentsResponse>('/agents')
      .then((data) => setAgents(data.agents ?? []))
      .catch(() => setError(true))
  }, [])

  const running = agents?.filter((a) => isUserSpawnedAgent(a) && isAgentActive(a)) ?? []

  return (
    <Card hover padding="sm" className="sm:p-6 cursor-pointer" onClick={() => navigate('/agents')}>
      <div className="flex items-center gap-2 mb-4">
        <Icon name="smart_toy" className="text-purple-400" size={20} />
        <h2 className="text-lg font-semibold">Active agents</h2>
      </div>
      {error ? (
        <p className="text-sm text-slate-500" data-testid="agents-error">Could not load agent count.</p>
      ) : agents === null ? (
        <p className="text-sm text-slate-500" data-testid="agents-loading">Loading...</p>
      ) : (
        <div data-testid="agents-content">
          <p className="text-3xl font-bold text-white mb-3" data-testid="agents-count">{running.length}</p>
          {running.length === 0 ? (
            <p className="text-sm text-slate-500">No agents running right now.</p>
          ) : (
            <ul className="space-y-1" data-testid="agents-list">
              {running.slice(0, 3).map((a) => (
                <li key={a.name} className="text-sm text-slate-400 truncate">
                  {a.task || a.description || a.name}
                </li>
              ))}
              {running.length > 3 && (
                <li className="text-xs text-slate-500">+{running.length - 3} more</li>
              )}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
