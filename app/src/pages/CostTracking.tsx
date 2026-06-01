import { useState, useEffect, useCallback, useMemo, useRef, type CSSProperties } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import TimeFilter, { type TimePeriod as Period } from '../components/TimeFilter'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'
import { useAppStore } from '../stores/app'

/** Contextual suggestions based on current numbers, ordered by severity. */
function SuggestionTips({ savings, totalTokens }: { savings: SavingsData; totalTokens: number }) {
  const cacheRate = savings.cache_efficiency_pct ?? 0
  const suggestions: { icon: string; color: string; text: string }[] = []

  if (totalTokens === 0) {
    suggestions.push({
      icon: 'tips_and_updates',
      color: 'text-slate-600 dark:text-slate-400',
      text: 'No usage yet. Spawn an agent or open chat to see stats.',
    })
  } else {
    if (cacheRate < 40) {
      suggestions.push({
        icon: 'warning',
        color: 'text-yellow-600 dark:text-yellow-400',
        text: 'Low cache reuse. The AI is re-reading the same setup every turn. Save your standing instructions in Settings so it remembers between messages.',
      })
    }
    if (cacheRate >= 40) {
      suggestions.push({
        icon: 'check_circle',
        color: 'text-green-600 dark:text-green-400',
        text: 'Cache reuse is healthy. Keep having longer multi-turn conversations to hold the rate high.',
      })
    }
  }

  // Cap at 3
  const shown = suggestions.slice(0, 3)

  return (
    <div data-testid="suggestions-section" className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-800">
      <p className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide mb-3">Suggestions</p>
      <div className="space-y-2">
        {shown.map((s, i) => (
          <div key={i} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
            <Icon name={s.icon} size={16} className={`${s.color} mt-0.5 shrink-0`} />
            <p>{s.text}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

interface WhatsWorkingSkill {
  id: string
  name: string
  uses_this_week: number
  prev_week_uses: number
}

interface WhatsWorkingRecommendation {
  id: string
  name: string
  why: string
}

interface WhatsWorkingThisWeek {
  agent_runs_completed: number
  agent_runs_failed: number
  top_spec_or_task: string | null
}

interface WhatsWorkingData {
  top_skills: WhatsWorkingSkill[]
  recommendations: WhatsWorkingRecommendation[]
  this_week: WhatsWorkingThisWeek
}

function wwSkillDelta(skill: WhatsWorkingSkill): { label: string; color: string } | null {
  if (skill.prev_week_uses === 0 && skill.uses_this_week > 0) {
    return { label: 'new', color: 'text-slate-600 dark:text-slate-400' }
  }
  if (skill.prev_week_uses > 0) {
    const pct = Math.round(((skill.uses_this_week - skill.prev_week_uses) / skill.prev_week_uses) * 100)
    return {
      label: pct >= 0 ? `+${pct}%` : `${pct}%`,
      color: pct > 0 ? 'text-green-600 dark:text-green-400' : pct < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-600 dark:text-slate-400',
    }
  }
  return null
}

function wwTruncatePriority(text: string): string {
  const colonIdx = text.indexOf(':')
  if (colonIdx > 0 && colonIdx <= 80) return text.slice(0, colonIdx)
  if (text.length <= 80) return text
  return text.slice(0, 80) + '…'
}

function WhatsWorkingPanel({
  data,
  loading,
  error,
  navigate,
  cardClass,
}: {
  data: WhatsWorkingData | null
  loading: boolean
  error: boolean
  navigate: (path: string) => void
  cardClass: string
}) {
  return (
    <div className={cardClass} data-testid="whats-working-section">
      <h2 className="text-lg font-semibold text-white mb-2">What's working</h2>
      <p className="text-slate-600 dark:text-slate-400 text-sm mb-6">A quick look at what you've been running this week and what to try next.</p>
      {loading ? (
        <p className="text-slate-600 dark:text-slate-400 text-sm">Loading...</p>
      ) : error || !data?.top_skills ? (
        <p className="text-slate-600 dark:text-slate-400 text-sm">Couldn't load your activity right now. Try refreshing.</p>
      ) : (
        <div className="space-y-8">
          <section aria-labelledby="ww-top-skills-heading">
            <h3 id="ww-top-skills-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
              What you used this week
            </h3>
            {data.top_skills.length > 0 ? (
              <div className="space-y-2">
                {data.top_skills.map((skill) => (
                  <div
                    key={skill.id}
                    data-testid="ww-skill-card"
                    className="flex items-center justify-between bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg px-4 py-3"
                  >
                    <div className="flex items-center gap-3">
                      <Icon name="bolt" className="text-amber-600 dark:text-amber-400 text-lg" />
                      <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{skill.name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {(() => {
                        const delta = wwSkillDelta(skill)
                        return delta ? (
                          <span className={`text-xs ${delta.color} bg-slate-200 dark:bg-slate-700/50 px-2 py-0.5 rounded-full`}>
                            {delta.label}
                          </span>
                        ) : null
                      })()}
                      <span className="text-xs text-slate-600 dark:text-slate-400 bg-slate-200 dark:bg-slate-700/50 px-2 py-0.5 rounded-full">
                        {skill.uses_this_week}x this week
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-2">
                {[
                  { id: 'builtin-builder', name: 'Builder', icon: 'engineering', desc: 'Build something new' },
                  { id: 'builtin-brainstorm', name: 'Brainstorm', icon: 'lightbulb', desc: 'Explore ideas and approaches' },
                  { id: 'builtin-research', name: 'Research', icon: 'search', desc: 'Look into a topic or question' },
                ].map((card) => (
                  <button
                    key={card.id}
                    type="button"
                    aria-label={`Start a ${card.name} agent`}
                    onClick={() => navigate(`/agents?template=${card.id}`)}
                    className="w-full text-left flex items-start gap-3 bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-3 hover:bg-slate-50/60 dark:hover:bg-slate-800/60 hover:border-slate-600/60 transition-colors cursor-pointer"
                  >
                    <Icon name={card.icon} className="text-sky-400 text-lg mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{card.name}</p>
                      <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">{card.desc}</p>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {data.recommendations.length > 0 && (
            <section aria-labelledby="ww-recs-heading">
              <h3 id="ww-recs-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                What to try next
              </h3>
              <div className="space-y-2">
                {data.recommendations.map((rec) => (
                  <button
                    key={rec.id}
                    type="button"
                    aria-label={`Start a ${rec.name} agent`}
                    onClick={() => navigate(`/agents?template=${rec.id}`)}
                    className="w-full text-left flex items-start gap-3 bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-3 hover:bg-slate-50/60 dark:hover:bg-slate-800/60 hover:border-slate-600/60 transition-colors cursor-pointer"
                  >
                    <Icon name="lightbulb" className="text-sky-400 text-lg mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{rec.name}</p>
                      <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">Because {rec.why}</p>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          )}

          <section aria-labelledby="ww-week-heading">
            <h3 id="ww-week-heading" className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
              This week
            </h3>
            <div className="bg-slate-100 dark:bg-slate-800/30 border border-slate-200/40 dark:border-slate-700/40 rounded-lg px-4 py-4 space-y-3">
              <div className="flex items-center gap-3">
                <Icon name="check_circle" className="text-green-600 dark:text-green-400 text-lg shrink-0" />
                <span className="text-sm text-slate-700 dark:text-slate-300">
                  {data.this_week.agent_runs_completed === 0
                    ? 'No agent runs finished this week yet.'
                    : `${data.this_week.agent_runs_completed} agent run${data.this_week.agent_runs_completed === 1 ? '' : 's'} finished${(data.this_week.agent_runs_failed ?? 0) > 0 ? `, ${data.this_week.agent_runs_failed} failed` : ''}`}
                </span>
              </div>
              {data.this_week.top_spec_or_task && (
                <div className="flex items-center gap-3">
                  <Icon name="flag" className="text-amber-600 dark:text-amber-400 text-lg shrink-0" />
                  <span className="text-sm text-slate-700 dark:text-slate-300">
                    Top priority: {wwTruncatePriority(data.this_week.top_spec_or_task)}
                  </span>
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

/** Contextual tips for improving token savings. */
function OptimizationTips({ savings }: { savings: SavingsData }) {
  const [showTips, setShowTips] = useState(false)
  const cacheRate = savings.cache_efficiency_pct ?? 0
  const compressionRate = savings.compression_pct ?? 0
  const convCache = savings.conversation_cache_pct ?? 0
  const tips: string[] = []

  if (cacheRate < 50) {
    tips.push('Keep longer conversations instead of starting new ones. Each new chat has to send the full context from scratch.')
  } else if (cacheRate < 80) {
    tips.push('Your cache hit rate is good. Reuse agent templates instead of writing new prompts each time to push it higher.')
  } else {
    tips.push('Your cache hit rate is excellent. ostk is reusing most of your context efficiently.')
  }
  if (compressionRate < 10) {
    tips.push('History compression is low right now. As your activity grows, compression kicks in automatically.')
  } else {
    tips.push('History compression is working well. Your audit trail is ' + compressionRate.toFixed(0) + '% smaller than raw.')
  }
  if (convCache < 1) {
    tips.push('Have longer multi-turn conversations to take advantage of conversation caching. Short one-off questions can\'t be cached.')
  } else {
    tips.push('Conversation caching is active. Longer sessions save more tokens as turns build on each other.')
  }
  tips.push('Use "saa" to spawn agents for complex tasks. Agents reuse cached system prompts across their entire run.')
  tips.push('Specs that decompose into multiple tasks get the best cache efficiency because every agent shares the spec context.')

  return (
    <div className="mt-4">
      <button
        onClick={() => setShowTips(!showTips)}
        className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors flex items-center gap-1"
      >
        <Icon name={showTips ? 'expand_less' : 'lightbulb'} size={16} />
        {showTips ? 'Hide tips' : 'How to save more tokens'}
      </button>
      {showTips && (
        <div className="mt-3 space-y-2">
          {tips.map((tip, i) => (
            <div key={i} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
              <Icon name="check_circle" size={16} className="text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
              <p>{tip}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Info icon that opens a rich popover explaining how a Usage-tab metric is
 * calculated. Fetches /api/costs/explain/{metric}?period={period} on first
 * open, then renders the formula, numerator, denominator, source, and a
 * "why this might surprise you" note.
 *
 * Keyboard accessible: Enter/Space toggle, Escape closes, focus returns to
 * the trigger. Announced to screen readers via role="dialog" and
 * aria-describedby on the content.
 */
interface ExplainPayload {
  metric: string
  formula: string
  numerator: { value: unknown; label: string; source: string }
  denominator: { value: unknown; label: string; source: string } | null
  result: unknown
  result_label: string
  window: string
  note?: string | null
}

function formatExplainValue(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') return v.toLocaleString()
  if (typeof v === 'string') return v
  if (typeof v === 'object') {
    // Object breakdown (e.g. input_tokens denominator)
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k.replace(/_/g, ' ')}: ${typeof val === 'number' ? val.toLocaleString() : String(val)}`)
      .join(', ')
  }
  return String(v)
}

function ExplainPopover({ metric, period, label }: { metric: string; period: string; label: string }) {
  const [open, setOpen] = useState(false)
  const [payload, setPayload] = useState<ExplainPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [position, setPosition] = useState<'above' | 'below'>('above')
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  // Track the last fetched (metric, period) key. If the same key opens again,
  // we show the existing data immediately and refresh silently in the background
  // so the popover never shows "Loading the math." on repeat opens.
  const fetchKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!open) return
    const key = `${metric}:${period}`
    // Only show the loading state when we have no data yet for this key.
    if (fetchKeyRef.current !== key) {
      setLoading(true)
      setError(null)
    }
    fetchKeyRef.current = key
    api.get<ExplainPayload>(`/costs/explain/${metric}?period=${period}`)
      .then((res) => {
        setPayload(res)
        setLoading(false)
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Could not load explanation')
        setLoading(false)
      })
  }, [open, metric, period])

  // Position the popover above or below based on actual viewport room.
  // The popover is tall (~420px when fully populated). Pick "above" only
  // when there is at least EXPLAIN_POPOVER_HEIGHT px of space above the
  // trigger. Otherwise flip below, so we never get clipped by the top of
  // the browser viewport (the address bar area).
  useEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const spaceAbove = rect.top
    const spaceBelow = window.innerHeight - rect.bottom
    const EXPLAIN_POPOVER_HEIGHT = 420
    if (spaceAbove >= EXPLAIN_POPOVER_HEIGHT) {
      setPosition('above')
    } else if (spaceBelow >= EXPLAIN_POPOVER_HEIGHT) {
      setPosition('below')
    } else {
      // Neither side fits cleanly. Pick whichever has more room; the
      // clamp in popoverStyle keeps the top edge inside the viewport.
      setPosition(spaceBelow > spaceAbove ? 'below' : 'above')
    }
  }, [open])

  // Close on Escape or outside click.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    function onClick(e: MouseEvent) {
      const t = e.target as Node
      if (
        popoverRef.current && !popoverRef.current.contains(t) &&
        triggerRef.current && !triggerRef.current.contains(t)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  const popoverStyle: CSSProperties = triggerRef.current
    ? (() => {
        const r = triggerRef.current.getBoundingClientRect()
        const width = 340
        // Keep the popover inside the viewport on the horizontal axis.
        const left = Math.max(
          8,
          Math.min(
            window.innerWidth - width - 8,
            r.left + r.width / 2 - width / 2,
          ),
        )
        if (position === 'above') {
          // "bottom: window.innerHeight - r.top + 8" would let the popover
          // grow upward past y=0 when it is taller than the space above the
          // trigger. Clamp so its top edge stays at least 8px from the top
          // of the viewport.
          const bottom = Math.min(
            window.innerHeight - r.top + 8,
            window.innerHeight - 8, // guarantees top >= 8
          )
          return { bottom, left, width, maxHeight: window.innerHeight - 16 }
        }
        // Below: guarantee the popover's top is at least 8px from the top
        // and capped so it does not overflow the viewport bottom either.
        const top = Math.max(8, r.bottom + 8)
        return { top, left, width, maxHeight: window.innerHeight - top - 8 }
      })()
    : {}

  return (
    <span className="relative inline-flex items-center ml-1">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Show how ${label} is calculated`}
        className="inline-flex items-center cursor-help rounded-full hover:bg-slate-100 dark:hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
        data-testid={`explain-trigger-${metric}`}
      >
        <Icon name="help_outline" size={14} className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors" />
      </button>
      {open && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label={`How ${label} is calculated`}
          className="fixed z-[9999] px-4 py-3 text-sm text-slate-800 dark:text-slate-200 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl"
          style={popoverStyle}
          data-testid={`explain-popover-${metric}`}
        >
          <div className="flex items-start justify-between mb-2 gap-2">
            <p className="font-semibold text-white">{label}</p>
            <button
              type="button"
              onClick={() => { setOpen(false); triggerRef.current?.focus() }}
              aria-label="Close"
              className="text-slate-600 dark:text-slate-400 hover:text-white -mt-0.5 -mr-1 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <Icon name="close" size={16} />
            </button>
          </div>
          {loading && <p className="text-xs text-slate-600 dark:text-slate-400">Loading the math.</p>}
          {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}
          {payload && (
            <div className="space-y-2">
              <div>
                <p className="text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">Formula</p>
                <p className="text-xs text-slate-800 dark:text-slate-200 leading-relaxed">{payload.formula}</p>
              </div>
              <div>
                <p className="text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">Result</p>
                <p className="text-lg font-bold text-blue-700 dark:text-blue-300" data-testid={`explain-result-${metric}`}>
                  {payload.result_label}
                </p>
              </div>
              <div>
                <p className="text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">
                  {payload.denominator ? 'Numerator' : 'Count'}
                </p>
                <p className="text-xs text-slate-800 dark:text-slate-200">
                  <span className="font-medium">{formatExplainValue(payload.numerator.value)}</span>
                  <span className="text-slate-600 dark:text-slate-400"> ({payload.numerator.label})</span>
                </p>
                <p className="text-xs text-slate-500 mt-0.5">Source: {payload.numerator.source}</p>
              </div>
              {payload.denominator && (
                <div>
                  <p className="text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">Denominator</p>
                  <p className="text-xs text-slate-800 dark:text-slate-200">
                    <span className="font-medium">{formatExplainValue(payload.denominator.value)}</span>
                    <span className="text-slate-600 dark:text-slate-400"> ({payload.denominator.label})</span>
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">Source: {payload.denominator.source}</p>
                </div>
              )}
              <div>
                <p className="text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">Window</p>
                <p className="text-xs text-slate-800 dark:text-slate-200">{payload.window}</p>
              </div>
              {payload.note && (
                <div className="pt-2 border-t border-slate-200 dark:border-slate-700">
                  <p className="text-xs text-slate-600 dark:text-slate-400 italic leading-relaxed">{payload.note}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </span>
  )
}

/** Small info icon that shows a tooltip on hover. */
function InfoTooltip({ text }: { text: string }) {
  const [visible, setVisible] = useState(false)
  const [position, setPosition] = useState<'above' | 'below'>('above')
  const iconRef = useRef<HTMLSpanElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function show() {
    // Pick direction based on actual viewport room. The tooltip is ~80px
    // tall (short text, 2-3 lines). Flip below only when there is not
    // enough space above, so it cannot get clipped by the top of the
    // viewport (e.g. tooltips at the top of the page).
    if (iconRef.current) {
      const rect = iconRef.current.getBoundingClientRect()
      const spaceAbove = rect.top
      const spaceBelow = window.innerHeight - rect.bottom
      const INFO_TOOLTIP_HEIGHT = 80
      if (spaceAbove >= INFO_TOOLTIP_HEIGHT) {
        setPosition('above')
      } else if (spaceBelow >= INFO_TOOLTIP_HEIGHT) {
        setPosition('below')
      } else {
        setPosition(spaceBelow > spaceAbove ? 'below' : 'above')
      }
    }
    timerRef.current = setTimeout(() => setVisible(true), 200)
  }

  function hide() {
    if (timerRef.current) clearTimeout(timerRef.current)
    setVisible(false)
  }

  return (
    <span
      ref={iconRef}
      className="relative inline-flex items-center ml-1 cursor-help"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      tabIndex={0}
      role="button"
      aria-label={text}
    >
      <Icon name="help_outline" size={14} className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors" />
      {visible && (
        <span
          className={`fixed z-[9999] w-56 px-3 py-2 text-xs leading-relaxed text-slate-800 dark:text-slate-200 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg pointer-events-none`}
          style={{
            ...(iconRef.current ? (() => {
              const r = iconRef.current.getBoundingClientRect()
              // Clamp the horizontal position so the tooltip never spills
              // past either edge of the viewport (width is w-56 = 224px).
              const left = Math.max(
                8,
                Math.min(window.innerWidth - 224 - 8, r.left + r.width / 2 - 112),
              )
              if (position === 'above') {
                // Clamp "bottom" so the tooltip's top edge never goes
                // above y=8 even if the trigger is near the top of the
                // viewport.
                const bottom = Math.min(
                  window.innerHeight - r.top + 8,
                  window.innerHeight - 8,
                )
                return { bottom, left }
              }
              const top = Math.max(8, r.bottom + 8)
              return { top, left }
            })() : {}),
          }}
          role="tooltip"
        >
          {text}
        </span>
      )}
    </span>
  )
}

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
  total_cost_usd?: number
  total_input_tokens: number
  // Combined input total that includes reused context (cache reads) and
  // cache creation. When prompt caching is on, almost all input tokens route
  // through cache reads, so this is the honest "what I actually sent in" number
  // for the Input tokens used tile. Optional to stay compatible with older
  // backends that do not send it yet.
  total_input_tokens_with_cache?: number
  total_output_tokens: number
  total_cache_read_tokens?: number
  total_cache_creation_tokens?: number
  // Share of inbound context that came from the prompt cache rather than being
  // re-sent fresh. Computed across the same dataset as the other tiles
  // (audit log + Claude Code transcripts), so "Context reuse rate" and
  // "Input tokens used" tell a consistent story. Falls back to
  // SavingsData.cache_efficiency_pct on older backends.
  context_reuse_pct?: number
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
  subscription_savings_usd?: number
  subscription_input_tokens?: number
  subscription_output_tokens?: number
  squash_tokens_saved?: number
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

const COST_CACHE_KEY = 'myos_cost_data_cache'
// Both cost and savings are cached per-period so switching pills paints
// instantly from localStorage without waiting for the network round trip.
const COST_CACHE_KEY_PREFIX = 'myos_cost_data_cache'
const SAVINGS_CACHE_KEY_PREFIX = 'myos_savings_data_cache'

function costCacheKey(period: Period): string {
  return `${COST_CACHE_KEY_PREFIX}_${period}`
}

function savingsCacheKey(period: Period): string {
  return `${SAVINGS_CACHE_KEY_PREFIX}_${period}`
}

// Legacy global cache (fallback for first render on existing installs).
function loadCachedCostData(): CostData | null {
  try {
    const raw = localStorage.getItem(COST_CACHE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as CostData
  } catch {
    return null
  }
}

function loadCachedCostDataForPeriod(period: Period): CostData | null {
  try {
    const raw = localStorage.getItem(costCacheKey(period))
    if (!raw) return loadCachedCostData()
    return JSON.parse(raw) as CostData
  } catch {
    return null
  }
}

function saveCostDataToCache(d: CostData, period?: Period) {
  try {
    // Write both the legacy global key (first-paint seed on fresh mounts)
    // and the per-period key (instant paint when switching filters).
    localStorage.setItem(COST_CACHE_KEY, JSON.stringify(d))
    if (period) {
      localStorage.setItem(costCacheKey(period), JSON.stringify(d))
    }
  } catch {
    // localStorage may be unavailable in some environments
  }
}

function loadCachedSavingsData(period: Period): SavingsData | null {
  try {
    const raw = localStorage.getItem(savingsCacheKey(period))
    if (!raw) return null
    return JSON.parse(raw) as SavingsData
  } catch {
    return null
  }
}

function saveSavingsDataToCache(d: SavingsData, period: Period) {
  try {
    localStorage.setItem(savingsCacheKey(period), JSON.stringify(d))
  } catch {
    // localStorage may be unavailable in some environments
  }
}

interface SubscriptionDailyEntry {
  date: string
  messages: number
  input_tokens: number
  output_tokens: number
}

interface SubscriptionProvider {
  auth_source: string
  messages_today: number
  tokens_today: number
  total_messages: number
  recent_daily: SubscriptionDailyEntry[]
  session_tokens?: Record<string, number>
  quota_available: boolean
  quota_note: string
}

interface SubscriptionData {
  claude: SubscriptionProvider
  gemini: SubscriptionProvider
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

function ProviderUsageCard({
  name,
  icon,
  color,
  data,
}: {
  name: string
  icon: string
  color: string
  data: SubscriptionProvider
}) {
  const todayTokens = data.tokens_today
  const maxMessages = Math.max(...data.recent_daily.map((d) => d.messages), 1)

  return (
    <div data-testid={`provider-card-${name.toLowerCase()}`} className="bg-slate-100 dark:bg-slate-800/50 rounded-xl p-5 border border-slate-200 dark:border-slate-700">
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-10 h-10 rounded-full ${color} flex items-center justify-center`}>
          <Icon name={icon} size={20} className="text-white" />
        </div>
        <div>
          <h3 className="font-semibold text-base">{name}</h3>
          <p className="text-xs text-slate-600 dark:text-slate-400 capitalize">{data.auth_source.replace(/_/g, ' ')}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-white/60 dark:bg-slate-900/60 rounded-lg p-3">
          <p className="text-xs text-slate-600 dark:text-slate-400 mb-1">Messages today</p>
          <p className="text-xl font-bold">{data.messages_today.toLocaleString()}</p>
        </div>
        <div className="bg-white/60 dark:bg-slate-900/60 rounded-lg p-3">
          <p className="text-xs text-slate-600 dark:text-slate-400 mb-1">Tokens today</p>
          <p className="text-xl font-bold">{todayTokens > 0 ? fmtTokens(todayTokens) : '—'}</p>
        </div>
      </div>

      <div className="mb-4">
        <p className="text-xs text-slate-600 dark:text-slate-400 mb-2">Messages, last 5 days</p>
        <div className="flex items-end gap-1 h-12">
          {data.recent_daily.map((d) => {
            const pct = maxMessages > 0 ? (d.messages / maxMessages) * 100 : 0
            return (
              <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                <div className="w-full flex items-end justify-center" style={{ height: '36px' }}>
                  <div
                    className={`w-full rounded-t transition-all ${color.replace('bg-', 'bg-')}`}
                    style={{ height: `${Math.max(pct, d.messages > 0 ? 8 : 0)}%`, minHeight: d.messages > 0 ? '4px' : '0' }}
                    title={`${d.date}: ${d.messages} messages`}
                  />
                </div>
                <span className="text-[9px] text-slate-500">{d.date.slice(5)}</span>
              </div>
            )
          })}
        </div>
      </div>

      {data.quota_available ? null : (
        <p className="text-xs text-slate-500 italic">{data.quota_note}</p>
      )}
    </div>
  )
}

export default function CostTracking() {
  // Period is read first so savings seed uses the correct per-period cache key.
  const [period, setPeriod] = useState<Period>(() => {
    const saved = localStorage.getItem('myos_cost_period') as Period | null
    return saved === 'today' || saved === 'week' || saved === 'month' || saved === 'all' ? saved : 'week'
  })
  // Seed from localStorage (per-period first, then legacy global) so both
  // first paint and every filter swap are instant.
  const [data, setData] = useState<CostData | null>(() => loadCachedCostDataForPeriod(period))
  // Seed savings from localStorage (per-period) so the tile paints immediately.
  const [savings, setSavings] = useState<SavingsData | null>(() => loadCachedSavingsData(period))
  // `loading` is only true when there is no data at all (first ever load).
  // `refreshing` is true during subsequent fetches so we can show a subtle
  // indicator without blanking the existing data.
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [fetchFailed, setFetchFailed] = useState(false)
  const [activeTab, setActiveTab] = useState<'spending' | 'whats-working' | 'subscription'>('spending')
  const navigate = useNavigate()
  const [wwData, setWwData] = useState<WhatsWorkingData | null>(null)
  const [wwLoading, setWwLoading] = useState(true)
  const [wwError, setWwError] = useState(false)
  const [subData, setSubData] = useState<SubscriptionData | null>(null)
  const [subLoading, setSubLoading] = useState(false)
  // fetchId increments on every period change. The effect captures its
  // own snapshot; if a newer fetch fires before an older one resolves,
  // the older one discards its stale response instead of overwriting state.
  const fetchIdRef = useRef(0)
  // savingsId increments on every period change so stale savings fetches are
  // discarded the same way stale cost fetches are.
  const savingsIdRef = useRef(0)

  const { showBudgetCaps } = useAppStore()

  const fetchData = useCallback(async (opts?: { initial?: boolean }) => {
    const myId = ++fetchIdRef.current
    if (opts?.initial && data !== null) {
      // We already have cached data; show a subtle refreshing spinner.
      setRefreshing(true)
    } else if (data === null) {
      setLoading(true)
    } else {
      setRefreshing(true)
    }
    try {
      const res = await api.get<CostData>(`/costs?period=${period}`)
      if (fetchIdRef.current !== myId) return // stale response, discard
      setData(res)
      saveCostDataToCache(res, period)
      setFetchFailed(false)
    } catch (e) {
      if (fetchIdRef.current !== myId) return
      reportError('Failed to fetch cost data', e)
      setFetchFailed(true)
    } finally {
      if (fetchIdRef.current === myId) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  useEffect(() => {
    fetchData({ initial: true })
  }, [fetchData])

  // When the period changes, paint cached data for that period instantly
  // so the filter swap feels immediate. The background refresh then comes
  // in via fetchData and replaces the cached paint with the live numbers.
  // This is the bulk of the "filter swap speed" win: no network wait before
  // the new period's numbers show up.
  useEffect(() => {
    const cached = loadCachedCostDataForPeriod(period)
    if (cached) setData(cached)
    // If there is no cached data for this period, keep whatever we had
    // on screen so the UI never blanks during the background fetch.
  }, [period])

  // Re-fetch savings whenever the period changes, keyed per period.
  useEffect(() => {
    const myId = ++savingsIdRef.current
    let cancelled = false
    // Paint from per-period localStorage cache immediately.
    const cached = loadCachedSavingsData(period)
    if (cached !== null) setSavings(cached)
    async function loadSavings() {
      try {
        const res = await api.get<SavingsData>(`/costs/savings?period=${period}`)
        if (!cancelled && savingsIdRef.current === myId) {
          setSavings(res)
          saveSavingsDataToCache(res, period)
        }
      } catch (e) {
        reportError('Failed to fetch savings data', e)
        if (!cancelled && savingsIdRef.current === myId) setSavings(prev => prev ?? { available: false })
      }
    }
    loadSavings()
    return () => {
      cancelled = true
    }
  }, [period])

  useEffect(() => {
    api.get<WhatsWorkingData>('/adoption/whats-working')
      .then((d) => { setWwData(d); setWwLoading(false) })
      .catch(() => { setWwError(true); setWwLoading(false) })
  }, [])

  useEffect(() => {
    if (activeTab !== 'subscription' || subData !== null) return
    setSubLoading(true)
    api.get<SubscriptionData>('/usage')
      .then((d) => { setSubData(d); setSubLoading(false) })
      .catch(() => setSubLoading(false))
  }, [activeTab, subData])

  // Derived values are memoized on the data reference so a filter swap (which
  // only replaces `data` with a cached object) does not re-run the reducers
  // and filters on every render pass. Same input produces reference-equal
  // output, keeping the render cost of a filter swap small.
  const trackedDays = useMemo(
    () => data?.by_date.filter(d => (d.input_tokens || 0) + (d.output_tokens || 0) > 0) ?? [],
    [data]
  )
  const maxDayTokens = useMemo(
    () => trackedDays.reduce((max, d) => Math.max(max, (d.input_tokens || 0) + (d.output_tokens || 0)), 0) || 1,
    [trackedDays]
  )

  const cardClass =
    'bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 p-6 rounded-xl hover:border-slate-200 dark:hover:border-slate-700 transition-colors'

  // Compute total tokens for the summary card. Use the combined input total
  // (raw input + cache creation + cache reads) so the output percentage in
  // the tile below is honest. Otherwise output looks huge compared to input
  // whenever prompt caching is on.
  const effectiveInputTokens = useMemo(
    () => data?.total_input_tokens_with_cache
      ?? ((data?.total_input_tokens ?? 0)
        + (data?.total_cache_creation_tokens ?? 0)
        + (data?.total_cache_read_tokens ?? 0)),
    [data]
  )
  const totalTokens = effectiveInputTokens + (data?.total_output_tokens ?? 0)
  const formattedTokens = totalTokens >= 1_000_000
    ? `${(totalTokens / 1_000_000).toFixed(1)}M`
    : totalTokens >= 1_000
      ? `${(totalTokens / 1_000).toFixed(0)}K`
      : String(totalTokens)

  // Reverse the agent list once per data change so the Usage History table
  // does not re-sort a 500+ row list on every single render.
  const reversedAgents = useMemo(
    () => (data?.agents ?? []).slice().reverse(),
    [data]
  )

  // Total token count across all models, used by the By Model percentages.
  // Cached so the table does not run a reduce() on every render.
  const modelTotalTokens = useMemo(
    () => (data?.by_model ?? []).reduce(
      (s, m) => s + (m.input_tokens || 0) + (m.output_tokens || 0),
      0
    ),
    [data]
  )

  function formatTokenCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
    return String(n)
  }

  /** Format a token count with K/M/B suffix, one decimal place. e.g. 1500 → "1.5K" */
  function formatTokens(n: number): string {
    if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
    return String(n)
  }

  /** Format seconds into a human-readable time string. e.g. 65 → "1.1 min", 8 → "8 sec" */
  function formatTimeSaved(seconds: number): string {
    if (seconds >= 60) return `${(seconds / 60).toFixed(1)} min`
    return `${Math.round(seconds)} sec`
  }

  return (
    <div className="min-h-dvh bg-white dark:bg-slate-950 text-slate-900 dark:text-white">
      <TopBar title="Usage" />

      <div className="px-4 pb-4 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4 sm:mb-6">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">Usage</h1>
            <p className="text-slate-600 dark:text-slate-400">
              Track usage across agents and chat
            </p>
          </div>
          <button
            onClick={() => fetchData()}
            disabled={refreshing}
            className="text-sm text-slate-600 dark:text-slate-400 hover:text-white transition-colors flex items-center gap-1 disabled:opacity-50"
          >
            <Icon name="refresh" size={16} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? 'Updating...' : 'Refresh'}
          </button>
        </div>
        {/* Tabs */}
        <div className="flex gap-1 border-b border-slate-200 dark:border-slate-800 mb-6 sm:mb-8">
          <button
            data-testid="tab-spending"
            onClick={() => setActiveTab('spending')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${activeTab === 'spending' ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-400' : 'text-slate-600 dark:text-slate-400 hover:text-white'}`}
          >
            Spending
          </button>
          <button
            data-testid="tab-whats-working"
            onClick={() => setActiveTab('whats-working')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${activeTab === 'whats-working' ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-400' : 'text-slate-600 dark:text-slate-400 hover:text-white'}`}
          >
            What's Working
          </button>
          <button
            data-testid="tab-subscription"
            onClick={() => setActiveTab('subscription')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${activeTab === 'subscription' ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-400' : 'text-slate-600 dark:text-slate-400 hover:text-white'}`}
          >
            Subscription
          </button>
        </div>

        <div className={activeTab !== 'spending' ? 'hidden' : ''}>
        {/* AI Summary */}
        {data && data.event_count > 0 && (
          <div className={`${cardClass} mb-6 border-l-4 border-blue-500`}>
            <div className="flex items-start gap-3">
              <Icon name="auto_awesome" className="text-blue-600 dark:text-blue-400 mt-0.5" size={20} />
              <div>
                <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
                  {`You've used ${data.agent_count} agents across ${data.event_count.toLocaleString()} events. `}
                  {totalTokens > 1_000_000
                    ? `That's ${formattedTokens} tokens of work done for you.`
                    : `${formattedTokens} tokens processed.`}
                  {savings?.conversation_cache_tokens && savings.conversation_cache_tokens > 0
                    ? ` ostk saved ${savings.conversation_cache_tokens.toLocaleString()} tokens through prompt caching and token compression.`
                    : savings?.subscription_input_tokens && savings.subscription_input_tokens > 0
                    ? ` ostk saved ${(savings.subscription_input_tokens).toLocaleString()} tokens through prompt caching and token compression.`
                    : ''}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Savings: ostk-only card. Anthropic cache values are intentionally
            hidden so this surface reads as ostk's own coordination layer win,
            not provider-native cache. */}
        <div className="mb-8">
          <div
            data-testid="ostk-savings-card"
            className={cardClass}
          >
            <div className="flex items-center gap-3 mb-4">
              <div className="w-12 h-12 rounded-full bg-green-500/20 flex items-center justify-center">
                <Icon name="savings" className="text-green-600 dark:text-green-400" size={28} />
              </div>
              <div>
                <h2 className="text-lg font-semibold">ostk savings</h2>
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  Tokens saved through ostk's context compression and reuse.
                </p>
              </div>
            </div>
            {savings === null ? (
              <p className="text-sm text-slate-500">Loading savings data...</p>
            ) : !savings.available ? (
              <p className="text-sm text-slate-600 dark:text-slate-400">Savings data not available yet.</p>
            ) : (() => {
              const compressionPct = savings.compression_pct ?? 0
              // needle recall and hay hit are always n/a for now (no real data)
              const hasCompression = compressionPct > 0

              // Tokens saved: conversation cache tokens + squash tokens saved
              const rawTokensSaved =
                (savings.conversation_cache_tokens ?? 0) +
                (savings.squash_tokens_saved ?? 0)
              const hasTokensSaved = rawTokensSaved > 0

              // Time saved: rough estimate at ~10K tokens/second of compute time
              const TOKEN_THROUGHPUT = 10_000
              const secondsSaved = rawTokensSaved / TOKEN_THROUGHPUT
              const hasTimeSaved = secondsSaved >= 1

              // All stats are zero/null: show empty state
              if (!hasCompression && !hasTokensSaved) {
                return (
                  <div data-testid="savings-no-data">
                    <p className="text-sm text-slate-600 dark:text-slate-400">
                      Savings will appear here as ostk compresses your context.
                    </p>
                  </div>
                )
              }

              // Count how many tiles will render to pick the right grid
              const tileCount = [hasCompression, hasTokensSaved, hasTimeSaved].filter(Boolean).length
              const gridClass = tileCount === 1
                ? 'grid grid-cols-1 gap-4'
                : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4'

              return (
                <div className={gridClass}>
                  {hasCompression && (
                    <div data-testid="stat-compression">
                      <p className="text-sm text-slate-600 dark:text-slate-400 mb-1">
                        Context compression
                        <ExplainPopover metric="context_compression" period={period} label="Context compression" />
                      </p>
                      <p className="text-3xl font-bold text-purple-600 dark:text-purple-400">
                        {compressionPct.toFixed(1)}%
                      </p>
                    </div>
                  )}
                  {hasTokensSaved && (
                    <div data-testid="stat-tokens-saved">
                      <p className="text-sm text-slate-600 dark:text-slate-400 mb-1">
                        Tokens saved
                        <ExplainPopover metric="tokens_saved" period={period} label="Tokens saved" />
                      </p>
                      <p className="text-3xl font-bold text-green-600 dark:text-green-400">
                        {formatTokens(rawTokensSaved)}
                      </p>
                    </div>
                  )}
                  {hasTimeSaved && (
                    <div data-testid="stat-time-saved">
                      <p className="text-sm text-slate-600 dark:text-slate-400 mb-1">
                        Time saved
                        <ExplainPopover metric="time_saved" period={period} label="Time saved" />
                      </p>
                      <p className="text-3xl font-bold text-blue-600 dark:text-blue-400">
                        {formatTimeSaved(secondsSaved)}
                      </p>
                    </div>
                  )}
                </div>
              )
            })()}
            {savings?.available && (
              <>
                <SuggestionTips savings={savings} totalTokens={totalTokens} />
                <OptimizationTips savings={savings} />
              </>
            )}
          </div>
        </div>

        {/* Time Filter */}
        <div className="mb-8">
          <TimeFilter
            value={period}
            onChange={(p) => { setPeriod(p); localStorage.setItem('myos_cost_period', p) }}
          />
          <p className="text-xs text-slate-500 mt-2">These are estimates based on per-token pricing for each model.</p>
        </div>

        {loading && !data ? (
          <p className="text-slate-500" data-testid="cost-loading">Loading cost data...</p>
        ) : !loading && !data && fetchFailed ? (
          <div className={`${cardClass} text-center py-12`} data-testid="cost-error">
            <Icon name="error_outline" className="text-red-500/60 mx-auto mb-3" size={48} />
            <p className="text-slate-300 text-lg mb-1">Couldn't load usage data</p>
            <p className="text-slate-500 text-sm mb-4">The backend didn't respond. It may still be starting up.</p>
            <button
              onClick={() => fetchData()}
              className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1 mx-auto"
              aria-label="Retry loading usage data"
            >
              <Icon name="refresh" size={16} />
              Try again
            </button>
          </div>
        ) : !data || data.event_count === 0 ? (
          <div className={`${cardClass} text-center py-12`}>
            <Icon name="payments" className="text-slate-600 mx-auto mb-3" size={48} />
            <p className="text-slate-600 dark:text-slate-400 text-lg mb-1">No spending data yet</p>
            <p className="text-slate-500 text-sm">
              Agent runs and chat messages will show up here.
            </p>
          </div>
        ) : (
          <>
            {/* Single Summary Card */}
            <div data-testid="summary-card" className={`${cardClass} mb-8`}>
              <div className="flex items-center gap-2 mb-4">
                <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                  <Icon name="insights" className="text-blue-600 dark:text-blue-400" size={20} />
                </div>
                <h2 className="text-lg font-semibold">
                  Summary
                  <span className="ml-2 text-sm font-normal text-slate-600 dark:text-slate-400">
                    {periodLabels[period]}
                  </span>
                </h2>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 sm:gap-6">
                {/* Agents */}
                <div>
                  <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                    Agents spawned
                    <ExplainPopover metric="agents_spawned" period={period} label="Agents spawned" />
                  </p>
                  <p className="text-3xl font-bold text-purple-600 dark:text-purple-400">
                    {data.agent_count}
                  </p>
                  <p className="text-xs text-slate-500 mt-1">
                    across {data.event_count.toLocaleString()} total calls
                  </p>
                </div>

                {/* Input tokens */}
                <div data-testid="input-tokens-tile">
                  <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                    Input tokens used<ExplainPopover metric="input_tokens" period={period} label="Input tokens used" />
                  </p>
                  <p className="text-3xl font-bold text-blue-600 dark:text-blue-400">
                    {formatTokenCount(
                      data.total_input_tokens_with_cache
                        ?? ((data.total_input_tokens ?? 0)
                          + (data.total_cache_creation_tokens ?? 0)
                          + (data.total_cache_read_tokens ?? 0))
                    )}
                  </p>
                  <p className="text-xs text-slate-500 mt-1">
                    {totalTokens > 0
                      ? (() => {
                          const pct = (effectiveInputTokens / totalTokens) * 100
                          return `${pct < 1 ? pct.toFixed(2) : pct.toFixed(1)}% of total`
                        })()
                      : 'from system + user messages'}
                  </p>
                </div>

                {/* Output tokens */}
                <div data-testid="output-tokens-tile">
                  <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                    Output tokens used<ExplainPopover metric="output_tokens" period={period} label="Output tokens used" />
                  </p>
                  <p className="text-3xl font-bold text-cyan-600 dark:text-cyan-400">
                    {formatTokenCount(data.total_output_tokens ?? 0)}
                  </p>
                  <p className="text-xs text-slate-500 mt-1">
                    {totalTokens > 0
                      ? (() => {
                          const pct = ((data.total_output_tokens ?? 0) / totalTokens) * 100
                          return `${pct < 1 ? pct.toFixed(2) : pct.toFixed(1)}% of total`
                        })()
                      : 'returned by the AI'}
                  </p>
                </div>

                {/* Cache hit rate - only if we have real data */}
                {(() => {
                  // Prefer the fleet-wide context_reuse_pct from /costs because
                  // it is computed on the same combined dataset (audit log +
                  // Claude Code transcripts) that produced the other tiles.
                  // Older backends do not send it, so fall back to the
                  // /costs/savings cache_efficiency_pct field.
                  const reuseFromCosts = data.context_reuse_pct
                  const cacheHitPct = reuseFromCosts != null
                    ? reuseFromCosts
                    : (data.total_input_tokens > 0 && savings?.cache_efficiency_pct != null
                        ? savings.cache_efficiency_pct
                        : null)
                  if (cacheHitPct === null) return null
                  const isHealthy = cacheHitPct >= 50
                  return (
                    <div>
                      <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                        Context reuse rate<ExplainPopover metric="context_reuse_pct" period={period} label="Context reuse rate" />
                      </p>
                      <p className={`text-3xl font-bold ${isHealthy ? 'text-green-600 dark:text-green-400' : 'text-yellow-600 dark:text-yellow-400'}`}>
                        {cacheHitPct.toFixed(1)}%
                      </p>
                      <p className="text-xs text-slate-500 mt-1">
                        {isHealthy ? 'Good reuse' : (
                          <Link to="/settings#standing-instructions-suggest" className="underline decoration-dotted hover:text-yellow-700 dark:hover:text-yellow-300">
                            Save standing instructions to raise this
                          </Link>
                        )}
                      </p>
                    </div>
                  )
                })()}

                {/* Budget caps section - only when showBudgetCaps is on */}
                {showBudgetCaps && (
                  <div data-testid="budget-caps-stat">
                    <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                      Budget caps set<InfoTooltip text="Total spending limits set when each agent was spawned. This is the cap, not actual money spent." />
                    </p>
                    <p className="text-3xl font-bold">
                      ${data.total_budget.toFixed(2)}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      ${data.agent_count > 0 ? (data.total_budget / data.agent_count).toFixed(2) : '0.00'} avg per agent
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Two Column Layout */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6 mb-8">
              {/* Spending Over Time Chart */}
              <div className={cardClass}>
                <h2 className="text-lg font-semibold mb-4">Usage Over Time<InfoTooltip text="Total tokens processed and API calls made, grouped by day." /></h2>
                {trackedDays.length === 0 ? (
                  <p className="text-sm text-slate-500">No data for this period.</p>
                ) : (
                  <div className="space-y-3">
                    {trackedDays.map((day) => {
                      const dayTokens = (day.input_tokens || 0) + (day.output_tokens || 0)
                      return (
                        <div key={day.date} className="flex items-center gap-3">
                          <span className="text-xs text-slate-600 dark:text-slate-400 w-16 shrink-0">
                            {formatDate(day.date)}
                          </span>
                          <div className="flex-1 h-8 bg-slate-100 dark:bg-slate-800 rounded-lg overflow-hidden relative">
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
                <h2 className="text-lg font-semibold mb-4">By Model<InfoTooltip text="Breakdown of token usage by AI model." /></h2>
                {data.by_model.length === 0 ? (
                  <p className="text-sm text-slate-500">No model data.</p>
                ) : (
                  <div className="space-y-4">
                    {(() => {
                      return data.by_model.map((m) => {
                        const modelTokens = (m.input_tokens || 0) + (m.output_tokens || 0)
                        const pct = modelTotalTokens > 0
                          ? ((modelTokens / modelTotalTokens) * 100).toFixed(0)
                          : '0'
                        const formattedModelTokens = modelTokens >= 1_000_000
                          ? `${(modelTokens / 1_000_000).toFixed(1)}M`
                          : modelTokens >= 1_000
                            ? `${(modelTokens / 1_000).toFixed(0)}K`
                            : String(modelTokens)
                        return (
                          <div key={m.model}>
                            <div className="flex items-center justify-between mb-1.5">
                              <span className="text-sm font-medium">{shortModelName(m.model)}</span>
                              <span className="text-sm text-slate-600 dark:text-slate-400">
                                {formattedModelTokens} tokens ({pct}%)
                              </span>
                            </div>
                            <div className="w-full h-2.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
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
                  <thead className="sticky top-0 bg-white dark:bg-slate-900">
                    <tr className="border-b border-slate-200 dark:border-slate-800">
                      <th className="text-left py-2 px-3 text-slate-600 dark:text-slate-400 font-medium">Name</th>
                      <th className="text-left py-2 px-3 text-slate-600 dark:text-slate-400 font-medium">Type</th>
                      <th className="text-left py-2 px-3 text-slate-600 dark:text-slate-400 font-medium">Model</th>
                      <th className="text-right py-2 px-3 text-slate-600 dark:text-slate-400 font-medium">Token Usage</th>
                      {showBudgetCaps && (
                        <th className="text-right py-2 px-3 text-slate-600 dark:text-slate-400 font-medium" data-testid="budget-col-header">Budget</th>
                      )}
                      <th className="text-right py-2 px-3 text-slate-600 dark:text-slate-400 font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reversedAgents.map((entry, i) => {
                      const isAgent = entry.event === 'agent.spawned'
                      const totalEntryTokens = (entry.input_tokens || 0) + (entry.output_tokens || 0)
                      return (
                        <tr
                          key={`${entry.name}-${entry.timestamp}-${i}`}
                          className="border-b border-slate-200 dark:border-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800/30 transition-colors"
                        >
                          <td className="py-2.5 px-3">
                            <div className="flex items-center gap-2">
                              <Icon
                                name={isAgent ? 'smart_toy' : 'chat'}
                                className={isAgent ? 'text-purple-600 dark:text-purple-400' : 'text-blue-600 dark:text-blue-400'}
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
                                ? 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
                                : 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
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
                          <td className="py-2.5 px-3 text-right font-mono text-slate-600 dark:text-slate-400">
                            {totalEntryTokens > 0
                              ? totalEntryTokens >= 1000
                                ? `${(totalEntryTokens / 1000).toFixed(0)}K`
                                : totalEntryTokens.toLocaleString()
                              : '-'}
                          </td>
                          {showBudgetCaps && (
                            <td className="py-2.5 px-3 text-right font-mono">
                              {entry.budget > 0 ? `$${entry.budget.toFixed(2)}` : '-'}
                            </td>
                          )}
                          <td className="py-2.5 px-3 text-right text-slate-600 dark:text-slate-400">
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
        {activeTab === 'whats-working' && (
          <WhatsWorkingPanel
            data={wwData}
            loading={wwLoading}
            error={wwError}
            navigate={navigate}
            cardClass={cardClass}
          />
        )}
        {activeTab === 'subscription' && (
          <div data-testid="subscription-panel">
            {subLoading && !subData ? (
              <p className="text-sm text-slate-600 dark:text-slate-400">Loading subscription data...</p>
            ) : !subData ? (
              <p className="text-sm text-slate-600 dark:text-slate-400">Subscription data unavailable.</p>
            ) : (
              <>
                <div className="mb-6">
                  <h2 className="text-lg font-semibold mb-1">Subscription usage</h2>
                  <p className="text-sm text-slate-600 dark:text-slate-400">Messages and tokens sent today and over the last 5 days, from your local activity log.</p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <ProviderUsageCard
                    name="Claude"
                    icon="auto_awesome"
                    color="bg-purple-500/20"
                    data={subData.claude}
                  />
                  <ProviderUsageCard
                    name="Gemini"
                    icon="stars"
                    color="bg-blue-500/20"
                    data={subData.gemini}
                  />
                </div>
                {subData.claude.session_tokens && Object.keys(subData.claude.session_tokens).length > 0 && (
                  <div className={`${cardClass} mt-4`}>
                    <h3 className="text-sm font-semibold mb-3 text-slate-700 dark:text-slate-300">Current session: Claude token snapshot</h3>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                      {(['cache_read', 'cache_create', 'output', 'billed'] as const).map((k) => {
                        const v = subData.claude.session_tokens?.[k] ?? 0
                        return v > 0 ? (
                          <div key={k} className="bg-slate-100 dark:bg-slate-800 rounded-lg p-3">
                            <p className="text-xs text-slate-600 dark:text-slate-400 mb-1 capitalize">{k.replace(/_/g, ' ')}</p>
                            <p className="font-bold">{fmtTokens(v)}</p>
                          </div>
                        ) : null
                      })}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
