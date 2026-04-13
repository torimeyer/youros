import { useState, useEffect, useCallback } from 'react'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import { api } from '../lib/api'

type Period = 'today' | 'week' | 'month' | 'all'

interface ModelBreakdown {
  model: string
  count: number
  total_budget: number
  input_tokens: number
  output_tokens: number
}

interface DateBreakdown {
  date: string
  count: number
  total_budget: number
  input_tokens: number
  output_tokens: number
}

interface CostEntry {
  name: string
  event: string
  model: string
  budget: number
  input_tokens: number
  output_tokens: number
  timestamp: string
  message_count?: number
}

interface TypeBreakdown {
  event: string
  count: number
  total_budget: number
  input_tokens: number
  output_tokens: number
}

interface CostData {
  total_budget: number
  total_input_tokens: number
  total_output_tokens: number
  event_count: number
  agent_count: number
  by_model: ModelBreakdown[]
  by_date: DateBreakdown[]
  by_type: TypeBreakdown[]
  agents: CostEntry[]
  period: string
}

interface SavingsData {
  available: boolean
  savings_usd?: number
  cache_efficiency_pct?: number
  compression_pct?: number
  cost_without_ostk_usd?: number
  cost_with_ostk_usd?: number
  conversation_cache_pct?: number
  conversation_cache_tokens?: number
  conversation_cache_read_tokens?: number
  conversation_cache_creation_tokens?: number
  period?: string
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
  'claude-opus-4-6': 'bg-rose-500',
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
    const versionParts: string[] = []
    for (let i = 2; i < parts.length; i++) {
      if (parts[i].length === 8 && /^\d{8}$/.test(parts[i])) break
      versionParts.push(parts[i])
    }
    const version = versionParts.join('.')
    return `${family} ${version}`
  }
  // "gemini-2.5-flash" -> "Gemini 2.5 Flash"
  if (parts[0] === 'gemini') {
    return parts.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ')
  }
  // Capitalize first letter of unknown models
  return model.charAt(0).toUpperCase() + model.slice(1)
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
  const [savings, setSavings] = useState<SavingsData | null>(null)

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

  useEffect(() => {
    let cancelled = false
    async function loadSavings() {
      try {
        const res = await api.get<SavingsData>('/costs/savings')
        if (!cancelled) setSavings(res)
      } catch (e) {
        console.error('Failed to fetch savings data:', e)
        if (!cancelled) setSavings({ available: false })
      }
    }
    loadSavings()
    return () => {
      cancelled = true
    }
  }, [])

  // Filter out days before token tracking started (they have budget but 0 tokens)
  const trackedDays = data?.by_date.filter(d => (d.input_tokens || 0) + (d.output_tokens || 0) > 0) ?? []
  const maxDayTokens = trackedDays.reduce((max, d) => Math.max(max, (d.input_tokens || 0) + (d.output_tokens || 0)), 0) || 1

  const cardClass =
    'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors'

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Cost Tracking" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6 sm:mb-8">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">AI Spending</h1>
            <p className="text-slate-400">
              Track usage across agents and chat
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

        {/* AI Summary */}
        {data && data.event_count > 0 && (
          <div className={`${cardClass} mb-6 border-l-4 border-blue-500`}>
            <div className="flex items-start gap-3">
              <Icon name="auto_awesome" className="text-blue-400 mt-0.5" size={20} />
              <div>
                <p className="text-sm text-slate-300 leading-relaxed">
                  {`You've used ${data.agent_count} agents across ${data.event_count.toLocaleString()} events. `}
                  {data.total_budget > 100
                    ? `$${data.total_budget.toFixed(0)} in agent budgets, spread across ${data.by_model?.length || 0} AI models. `
                    : `$${data.total_budget.toFixed(2)} in agent budgets so far. `}
                  {data.total_input_tokens + data.total_output_tokens > 1_000_000
                    ? `That's ${((data.total_input_tokens + data.total_output_tokens) / 1_000_000).toFixed(1)}M tokens of work done for you.`
                    : `${((data.total_input_tokens + data.total_output_tokens) / 1000).toFixed(0)}K tokens processed.`}
                  {savings?.savings_usd && savings.savings_usd > 0
                    ? ` myOS saved you $${savings.savings_usd.toFixed(2)} by reusing cached work.`
                    : ''}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* myOS Savings Tile */}
        <div
          data-testid="myos-savings-tile"
          className={`${cardClass} mb-8`}
        >
          <div className="flex items-center gap-2 mb-4">
            <div className="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
              <Icon name="savings" className="text-green-400" size={20} />
            </div>
            <h2 className="text-lg font-semibold">myOS savings</h2>
          </div>
          {savings === null ? (
            <p className="text-sm text-slate-500">Loading savings data...</p>
          ) : !savings.available ? (
            <p className="text-sm text-slate-400">Savings data not available yet.</p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 sm:gap-6">
              <div>
                <p className="text-sm text-slate-400 mb-2">
                  myOS saved you this session
                </p>
                <p className="text-3xl font-bold text-green-400">
                  ${(savings.savings_usd ?? 0).toFixed(4)}
                </p>
              </div>
              <div>
                <p className="text-sm text-slate-400 mb-2">
                  Requests reused from memory
                </p>
                <p className="text-3xl font-bold">
                  {(savings.cache_efficiency_pct ?? 0).toFixed(1)}%
                </p>
              </div>
              <div>
                <p className="text-sm text-slate-400 mb-2">
                  Space saved on stored information
                </p>
                <p className="text-3xl font-bold">
                  {(savings.compression_pct ?? 0).toFixed(1)}%
                </p>
              </div>
              <div>
                <p className="text-sm text-slate-400 mb-2">
                  Conversation cache hits
                </p>
                <p className="text-3xl font-bold text-blue-400">
                  {(savings.conversation_cache_pct ?? 0).toFixed(1)}%
                </p>
                {(savings.conversation_cache_tokens ?? 0) > 0 && (
                  <p className="text-xs text-slate-500 mt-1">
                    {((savings.conversation_cache_tokens ?? 0) >= 1000
                      ? `${((savings.conversation_cache_tokens ?? 0) / 1000).toFixed(0)}K`
                      : (savings.conversation_cache_tokens ?? 0).toLocaleString())} tokens from cache
                  </p>
                )}
              </div>
            </div>
          )}
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
        ) : !data || data.event_count === 0 ? (
          <div className={`${cardClass} text-center py-12`}>
            <Icon name="payments" className="text-slate-600 mx-auto mb-3" size={48} />
            <p className="text-slate-400 text-lg mb-1">No spending data yet</p>
            <p className="text-slate-500 text-sm">
              Agent runs and chat messages will show up here.
            </p>
          </div>
        ) : (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 sm:gap-6 mb-8">
              {/* Total Spend */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                    <Icon name="payments" className="text-blue-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Total Spend</p>
                </div>
                <p className="text-3xl font-bold">${data.total_budget.toFixed(2)}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {periodLabels[period]}
                </p>
              </div>

              {/* Cost per Agent */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                    <Icon name="smart_toy" className="text-purple-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Cost per Agent</p>
                </div>
                <p className="text-3xl font-bold">
                  {data.agent_count > 0
                    ? `$${(data.total_budget / data.agent_count).toFixed(2)}`
                    : '$0.00'}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  {data.agent_count} agent{data.agent_count !== 1 ? 's' : ''} spawned
                </p>
              </div>

              {/* Cost per Call */}
              <div className={cardClass}>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-10 h-10 rounded-full bg-cyan-500/20 flex items-center justify-center">
                    <Icon name="bolt" className="text-cyan-400" size={20} />
                  </div>
                  <p className="text-sm text-slate-400">Cost per Call</p>
                </div>
                <p className="text-3xl font-bold">
                  {data.event_count > 0
                    ? `$${(data.total_budget / data.event_count).toFixed(4)}`
                    : '$0.00'}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  {data.event_count.toLocaleString()} calls total
                </p>
              </div>

              {/* Prompt Efficiency */}
              {(() => {
                const ratio = data.total_output_tokens > 0
                  ? data.total_input_tokens / data.total_output_tokens
                  : 0
                const isHealthy = ratio > 0 && ratio <= 3
                const isOk = ratio > 3 && ratio <= 5
                return (
                  <div className={cardClass}>
                    <div className="flex items-center gap-2 mb-3">
                      <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
                        isHealthy ? 'bg-green-500/20' : isOk ? 'bg-yellow-500/20' : 'bg-orange-500/20'
                      }`}>
                        <Icon name="speed" className={
                          isHealthy ? 'text-green-400' : isOk ? 'text-yellow-400' : 'text-orange-400'
                        } size={20} />
                      </div>
                      <p className="text-sm text-slate-400">Prompt Efficiency</p>
                    </div>
                    <p className={`text-3xl font-bold ${
                      isHealthy ? 'text-green-400' : isOk ? 'text-yellow-400' : 'text-orange-400'
                    }`}>
                      {ratio > 0 ? `${ratio.toFixed(1)}:1` : 'N/A'}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      {isHealthy ? 'Healthy' : isOk ? 'Could be leaner' : 'Prompts may be bloated'}
                    </p>
                  </div>
                )
              })()}
            </div>

            {/* Two Column Layout */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6 mb-8">
              {/* Spending Over Time Chart */}
              <div className={cardClass}>
                <h2 className="text-lg font-semibold mb-4">Usage Over Time</h2>
                {trackedDays.length === 0 ? (
                  <p className="text-sm text-slate-500">No data for this period.</p>
                ) : (
                  <div className="space-y-3">
                    {trackedDays.map((day) => {
                      const dayTokens = (day.input_tokens || 0) + (day.output_tokens || 0)
                      return (
                        <div key={day.date} className="flex items-center gap-3">
                          <span className="text-xs text-slate-400 w-16 shrink-0">
                            {formatDate(day.date)}
                          </span>
                          <div className="flex-1 h-8 bg-slate-800 rounded-lg overflow-hidden relative">
                            <div
                              className="h-full bg-blue-500 rounded-lg transition-all duration-500"
                              style={{
                                width: `${maxDayTokens > 0 ? (dayTokens / maxDayTokens) * 100 : 0}%`,
                                minWidth: dayTokens > 0 ? '2px' : '0',
                              }}
                            />
                            <span className="absolute inset-0 flex items-center px-3 text-xs font-medium text-white">
                              {dayTokens >= 1000 ? `${(dayTokens / 1000).toFixed(0)}K tokens` : `${dayTokens} tokens`} ({day.count} call{day.count !== 1 ? 's' : ''})
                            </span>
                          </div>
                        </div>
                      )
                    })}
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
                    {(() => {
                      const totalTokens = data.by_model.reduce((s, m) => s + (m.input_tokens || 0) + (m.output_tokens || 0), 0)
                      const useBudget = data.total_budget > 0
                      return data.by_model.map((m) => {
                        const modelTokens = (m.input_tokens || 0) + (m.output_tokens || 0)
                        const pct = useBudget
                          ? ((m.total_budget / data.total_budget) * 100).toFixed(0)
                          : totalTokens > 0
                            ? ((modelTokens / totalTokens) * 100).toFixed(0)
                            : '0'
                        const formattedTokens = modelTokens >= 1_000_000
                          ? `${(modelTokens / 1_000_000).toFixed(1)}M`
                          : modelTokens >= 1_000
                            ? `${(modelTokens / 1_000).toFixed(0)}K`
                            : String(modelTokens)
                        return (
                          <div key={m.model}>
                            <div className="flex items-center justify-between mb-1.5">
                              <span className="text-sm font-medium">{shortModelName(m.model)}</span>
                              <span className="text-sm text-slate-400">
                                {useBudget ? `$${m.total_budget.toFixed(2)}` : `${formattedTokens} tokens`} ({pct}%)
                              </span>
                            </div>
                            <div className="w-full h-2.5 bg-slate-800 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${getModelColor(m.model)} transition-all duration-500`}
                                style={{ width: `${pct}%`, minWidth: Number(pct) > 0 ? '4px' : '0' }}
                              />
                            </div>
                            <p className="text-xs text-slate-500 mt-1">
                              {m.count} call{m.count !== 1 ? 's' : ''}
                            </p>
                          </div>
                        )
                      })
                    })()}
                  </div>
                )}
              </div>
            </div>

            {/* Usage History Table */}
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-4">Usage History</h2>
              <div className="overflow-x-auto" style={{ maxHeight: '500px', overflowY: 'auto' }}>
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-900">
                    <tr className="border-b border-slate-800">
                      <th className="text-left py-2 px-3 text-slate-400 font-medium">Name</th>
                      <th className="text-left py-2 px-3 text-slate-400 font-medium">Type</th>
                      <th className="text-left py-2 px-3 text-slate-400 font-medium">Model</th>
                      <th className="text-right py-2 px-3 text-slate-400 font-medium">Token Usage</th>
                      <th className="text-right py-2 px-3 text-slate-400 font-medium">Budget</th>
                      <th className="text-right py-2 px-3 text-slate-400 font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.agents.slice().reverse().map((entry, i) => {
                      const isAgent = entry.event === 'agent.spawned'
                      const totalTokens = (entry.input_tokens || 0) + (entry.output_tokens || 0)
                      return (
                        <tr
                          key={`${entry.name}-${entry.timestamp}-${i}`}
                          className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors"
                        >
                          <td className="py-2.5 px-3">
                            <div className="flex items-center gap-2">
                              <Icon
                                name={isAgent ? 'smart_toy' : 'chat'}
                                className={isAgent ? 'text-purple-400' : 'text-blue-400'}
                                size={16}
                              />
                              <span className="font-medium">{entry.name}</span>
                              {!isAgent && (entry.message_count ?? 0) > 1 && (
                                <span className="text-xs text-slate-500">
                                  ({entry.message_count} messages)
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                              isAgent
                                ? 'bg-purple-500/20 text-purple-400'
                                : 'bg-blue-500/20 text-blue-400'
                            }`}>
                              {isAgent ? 'Agent' : 'Chat'}
                            </span>
                          </td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`text-xs px-2 py-0.5 rounded-full font-medium ${getModelBadgeColor(entry.model)}`}
                            >
                              {shortModelName(entry.model)}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono text-slate-400">
                            {totalTokens > 0
                              ? totalTokens >= 1000
                                ? `${(totalTokens / 1000).toFixed(0)}K`
                                : totalTokens.toLocaleString()
                              : '-'}
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono">
                            {entry.budget > 0 ? `$${entry.budget.toFixed(2)}` : '-'}
                          </td>
                          <td className="py-2.5 px-3 text-right text-slate-400">
                            {formatTimestamp(entry.timestamp)}
                          </td>
                        </tr>
                      )
                    })}
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
