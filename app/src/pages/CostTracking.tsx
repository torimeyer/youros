import { useState, useEffect, useCallback } from 'react'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import { api } from '../lib/api'

type Period = 'today' | 'week' | 'month' | 'all'

interface ModelBreakdown {
  model: string
  count: number
  total_budget: number
}

interface DateBreakdown {
  date: string
  count: number
  total_budget: number
}

interface AgentEntry {
  name: string
  model: string
  budget: number
  timestamp: string
}

interface CostData {
  total_budget: number
  agent_count: number
  by_model: ModelBreakdown[]
  by_date: DateBreakdown[]
  agents: AgentEntry[]
  period: string
}

const periodLabels: Record<Period, string> = {
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
  all: 'All Time',
}

const modelColors: Record<string, string> = {
  'claude-sonnet-4-5-20250929': 'bg-purple-500',
  'claude-opus-4-5-20250929': 'bg-pink-500',
  'claude-haiku-3-5-20241022': 'bg-cyan-500',
  'claude-sonnet-4-20250514': 'bg-blue-500',
  'claude-opus-4-20250514': 'bg-rose-500',
}

function getModelColor(model: string): string {
  if (modelColors[model]) return modelColors[model]
  // Assign a color based on model name hash
  const colors = ['bg-purple-500', 'bg-blue-500', 'bg-cyan-500', 'bg-pink-500', 'bg-orange-500', 'bg-green-500']
  let hash = 0
  for (let i = 0; i < model.length; i++) {
    hash = model.charCodeAt(i) + ((hash << 5) - hash)
  }
  return colors[Math.abs(hash) % colors.length]
}

function getModelBadgeColor(model: string): string {
  const barColor = getModelColor(model)
  return barColor.replace('bg-', 'bg-').replace('-500', '-500/20') + ' ' + barColor.replace('bg-', 'text-').replace('-500', '-400')
}

function shortModelName(model: string): string {
  // Turn "claude-sonnet-4-5-20250929" into "Sonnet 4.5"
  const parts = model.split('-')
  if (parts.length >= 3 && parts[0] === 'claude') {
    const family = parts[1].charAt(0).toUpperCase() + parts[1].slice(1)
    // Try to build version from remaining parts before the date
    const versionParts: string[] = []
    for (let i = 2; i < parts.length; i++) {
      if (parts[i].length === 8 && /^\d{8}$/.test(parts[i])) break
      versionParts.push(parts[i])
    }
    const version = versionParts.join('.')
    return `${family} ${version}`
  }
  return model
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + 'T00:00:00')
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return ts
  }
}

export default function CostTracking() {
  const [data, setData] = useState<CostData | null>(null)
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState<Period>('all')

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get<CostData>(`/costs?period=${period}`)
      setData(res)
    } catch (e) {
      console.error('Failed to fetch cost data:', e)
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const maxDayBudget = data?.by_date.reduce((max, d) => Math.max(max, d.total_budget), 0) ?? 1

  const cardClass =
    'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors'

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Cost Tracking" />

      <div className="pt-16 p-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold mb-1">AI Spending</h1>
            <p className="text-slate-400">
              Track budget allocated to your AI agents
            </p>
          </div>
          <button
            onClick={fetchData}
            className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1"
          >
            <Icon name="refresh" size={16} />
            Refresh
          </button>
        </div>

        {/* Time Filter Buttons */}
        <div className="flex gap-2 mb-8">
          {(Object.keys(periodLabels) as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                period === p
                  ? 'bg-blue-500/20 text-blue-400 border border-blue-500/50'
                  : 'bg-slate-900 text-slate-400 border border-slate-800 hover:border-slate-700 hover:text-slate-300'
              }`}
            >
              {periodLabels[p]}
            </button>
          ))}
        </div>

        {loading && !data ? (
          <p className="text-slate-500">Loading cost data...</p>
        ) : !data || data.agent_count === 0 ? (
          <div className={`${cardClass} text-center py-12`}>
            <Icon name="payments" className="text-slate-600 mx-auto mb-3" size={48} />
            <p className="text-slate-400 text-lg mb-1">No agent spending data yet</p>
            <p className="text-slate-500 text-sm">
              When you spawn agents, their budget allocations will show up here.
            </p>
          </div>
        ) : (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-3 gap-6 mb-8">
              {/* Total Budget */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                    <Icon name="payments" className="text-blue-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Total Budget Allocated</p>
                </div>
                <p className="text-3xl font-bold">${data.total_budget.toFixed(2)}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {periodLabels[period]}
                </p>
              </div>

              {/* Agent Count */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                    <Icon name="smart_toy" className="text-purple-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Agents Spawned</p>
                </div>
                <p className="text-3xl font-bold">{data.agent_count}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {periodLabels[period]}
                </p>
              </div>

              {/* Average Budget */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-cyan-500/20 flex items-center justify-center">
                    <Icon name="analytics" className="text-cyan-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Average Budget per Agent</p>
                </div>
                <p className="text-3xl font-bold">
                  ${data.agent_count > 0
                    ? (data.total_budget / data.agent_count).toFixed(2)
                    : '0.00'}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  {periodLabels[period]}
                </p>
              </div>
            </div>

            {/* Two Column Layout */}
            <div className="grid grid-cols-2 gap-6 mb-8">
              {/* Spending Over Time Chart */}
              <div className={cardClass}>
                <h2 className="text-lg font-semibold mb-4">Budget Allocation Over Time</h2>
                {data.by_date.length === 0 ? (
                  <p className="text-sm text-slate-500">No data for this period.</p>
                ) : (
                  <div className="space-y-3">
                    {data.by_date.map((day) => (
                      <div key={day.date} className="flex items-center gap-3">
                        <span className="text-xs text-slate-400 w-16 shrink-0">
                          {formatDate(day.date)}
                        </span>
                        <div className="flex-1 h-8 bg-slate-800 rounded-lg overflow-hidden relative">
                          <div
                            className="h-full bg-blue-500 rounded-lg transition-all duration-500"
                            style={{
                              width: `${maxDayBudget > 0 ? (day.total_budget / maxDayBudget) * 100 : 0}%`,
                              minWidth: day.total_budget > 0 ? '2px' : '0',
                            }}
                          />
                          <span className="absolute inset-0 flex items-center px-3 text-xs font-medium text-white">
                            ${day.total_budget.toFixed(2)} ({day.count} agent{day.count !== 1 ? 's' : ''})
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Model Breakdown */}
              <div className={cardClass}>
                <h2 className="text-lg font-semibold mb-4">By Model</h2>
                {data.by_model.length === 0 ? (
                  <p className="text-sm text-slate-500">No model data.</p>
                ) : (
                  <div className="space-y-4">
                    {data.by_model.map((m) => {
                      const pct = data.total_budget > 0
                        ? ((m.total_budget / data.total_budget) * 100).toFixed(0)
                        : '0'
                      return (
                        <div key={m.model}>
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-sm font-medium">{shortModelName(m.model)}</span>
                            <span className="text-sm text-slate-400">
                              ${m.total_budget.toFixed(2)} ({pct}%)
                            </span>
                          </div>
                          <div className="w-full h-2.5 bg-slate-800 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${getModelColor(m.model)} transition-all duration-500`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <p className="text-xs text-slate-500 mt-1">
                            {m.count} agent{m.count !== 1 ? 's' : ''} spawned
                          </p>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Agent History Table */}
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-4">Agent History</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="text-left py-2 px-3 text-slate-400 font-medium">Agent</th>
                      <th className="text-left py-2 px-3 text-slate-400 font-medium">Model</th>
                      <th className="text-right py-2 px-3 text-slate-400 font-medium">Budget</th>
                      <th className="text-right py-2 px-3 text-slate-400 font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.agents.map((agent, i) => (
                      <tr
                        key={`${agent.name}-${agent.timestamp}-${i}`}
                        className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors"
                      >
                        <td className="py-2.5 px-3">
                          <div className="flex items-center gap-2">
                            <Icon name="smart_toy" className="text-purple-400" size={16} />
                            <span className="font-medium">{agent.name}</span>
                          </div>
                        </td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full font-medium ${getModelBadgeColor(agent.model)}`}
                          >
                            {shortModelName(agent.model)}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono">
                          ${agent.budget.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-right text-slate-400">
                          {formatTimestamp(agent.timestamp)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
