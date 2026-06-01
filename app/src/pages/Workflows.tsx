import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import { api } from '../lib/api'
import { Button, EmptyState, Card, ErrorBanner } from '../components/ui'
import { useWorkflowsStore } from '../stores/workflowsStore'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface WorkflowStep {
  id: string
  agent_name: string
  prompt: string
  model: string
  budget: number
  depends_on: string[]
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  started_at?: string
  finished_at?: string
  error?: string
}

interface Workflow {
  id: string
  name: string
  steps: WorkflowStep[]
  status: 'pending' | 'running' | 'done' | 'failed'
  created_at: string
  completed_at?: string
}

interface RunStep {
  id: string
  agent_name: string
  status: string
  started_at?: string
  finished_at?: string
  error?: string
}

interface WorkflowRun {
  run_id: string
  status: 'done' | 'failed' | 'running' | string
  started_at?: string
  completed_at?: string
  duration_seconds?: number | null
  steps: RunStep[]
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; label: string }> = {
    pending: { color: 'text-slate-600 dark:text-slate-400 bg-slate-100 dark:bg-slate-800', label: 'Waiting' },
    running: { color: 'text-blue-600 dark:text-blue-400 bg-blue-500/20', label: 'Running' },
    done: { color: 'text-green-600 dark:text-green-400 bg-green-500/20', label: 'Done' },
    failed: { color: 'text-red-600 dark:text-red-400 bg-red-500/20', label: 'Failed' },
    skipped: { color: 'text-yellow-600 dark:text-yellow-400 bg-yellow-500/20', label: 'Skipped' },
  }
  const style = map[status] ?? map['pending']
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${style.color}`}>
      {style.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Step icon
// ---------------------------------------------------------------------------

function StepIcon({ status }: { status: string }) {
  if (status === 'done') return <Icon name="check_circle" className="text-green-600 dark:text-green-400 text-lg" />
  if (status === 'running') return <Icon name="pending" className="text-blue-600 dark:text-blue-400 text-lg animate-pulse" />
  if (status === 'failed') return <Icon name="cancel" className="text-red-600 dark:text-red-400 text-lg" />
  if (status === 'skipped') return <Icon name="remove_circle" className="text-yellow-600 dark:text-yellow-400 text-lg" />
  return <Icon name="radio_button_unchecked" className="text-slate-600 text-lg" />
}

// ---------------------------------------------------------------------------
// Run history drawer (inline per workflow row)
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}

function RunsDrawer({ workflowId }: { workflowId: string }) {
  const [runs, setRuns] = useState<WorkflowRun[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await api.get<{ runs: WorkflowRun[] }>(`/workflows/${workflowId}/runs`)
        if (!cancelled) setRuns(res.runs)
      } catch {
        if (!cancelled) setRuns([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [workflowId])

  if (loading) {
    return (
      <div className="px-4 pb-3 text-xs text-slate-500">Loading run history...</div>
    )
  }

  if (!runs || runs.length === 0) {
    return (
      <div className="px-4 pb-3 text-xs text-slate-500">
        No runs yet. Click Run to execute this automation.
      </div>
    )
  }

  return (
    <div className="px-4 pb-3 space-y-2">
      <p className="text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">Recent runs</p>
      {runs.map((run) => {
        const isExpanded = expandedRun === run.run_id
        const statusColors: Record<string, string> = {
          done: 'text-green-600 dark:text-green-400 bg-green-500/10',
          failed: 'text-red-600 dark:text-red-400 bg-red-500/10',
          running: 'text-blue-600 dark:text-blue-400 bg-blue-500/10',
        }
        const statusColor = statusColors[run.status] ?? 'text-slate-600 dark:text-slate-400 bg-slate-100 dark:bg-slate-800'
        const startedLabel = run.started_at
          ? new Date(run.started_at).toLocaleString()
          : 'Unknown start'

        return (
          <div
            key={run.run_id}
            className="rounded-lg border border-slate-200 dark:border-slate-700/50 bg-slate-50/40 dark:bg-slate-800/40 overflow-hidden"
          >
            {/* Run summary row */}
            <button
              className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-slate-50/60 dark:hover:bg-slate-800/60 transition-colors"
              onClick={() => setExpandedRun(isExpanded ? null : run.run_id)}
            >
              <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${statusColor}`}>
                {run.status === 'done' ? 'Success' : run.status === 'failed' ? 'Failed' : run.status}
              </span>
              <span className="text-xs text-slate-700 dark:text-slate-300 flex-1">{startedLabel}</span>
              {run.duration_seconds != null && (
                <span className="text-xs text-slate-500 shrink-0">
                  {formatDuration(run.duration_seconds)}
                </span>
              )}
              <Icon
                name={isExpanded ? 'expand_less' : 'expand_more'}
                className="text-slate-500 text-base shrink-0"
              />
            </button>

            {/* Expanded step breakdown */}
            {isExpanded && (
              <div className="border-t border-slate-200 dark:border-slate-700/50 px-3 py-2 space-y-1.5">
                {run.steps.map((step) => {
                  const stepColors: Record<string, string> = {
                    done: 'text-green-600 dark:text-green-400',
                    failed: 'text-red-600 dark:text-red-400',
                    skipped: 'text-yellow-600 dark:text-yellow-400',
                    running: 'text-blue-600 dark:text-blue-400',
                  }
                  const stepColor = stepColors[step.status] ?? 'text-slate-500'
                  const stepDuration =
                    step.started_at && step.finished_at
                      ? formatDuration(
                          Math.round(
                            (new Date(step.finished_at).getTime() -
                              new Date(step.started_at).getTime()) /
                              1000
                          )
                        )
                      : null

                  return (
                    <div key={step.id} className="flex items-start gap-2">
                      <Icon
                        name={
                          step.status === 'done'
                            ? 'check_circle'
                            : step.status === 'failed'
                            ? 'cancel'
                            : step.status === 'skipped'
                            ? 'remove_circle'
                            : 'radio_button_unchecked'
                        }
                        className={`text-sm shrink-0 mt-0.5 ${stepColor}`}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {/* Link to agent row on the Agents page */}
                          <Link
                            to={`/agents?highlight=${encodeURIComponent(step.agent_name)}`}
                            className="text-xs text-slate-700 dark:text-slate-300 hover:text-white hover:underline transition-colors"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {step.agent_name}
                          </Link>
                          <span className={`text-[10px] ${stepColor}`}>{step.status}</span>
                          {stepDuration && (
                            <span className="text-[10px] text-slate-600">{stepDuration}</span>
                          )}
                        </div>
                        {step.error && (
                          <p className="text-[11px] text-red-600 dark:text-red-400 mt-0.5">{step.error}</p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Workflow detail view (step-by-step progress)
// ---------------------------------------------------------------------------

function WorkflowDetail({
  workflow,
  onClose,
  onRun,
  onDelete,
}: {
  workflow: Workflow
  onClose: () => void
  onRun: (id: string) => void
  onDelete: (id: string) => void
}) {
  // Escape closes the detail view.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-2xl w-full max-w-2xl p-6 shadow-xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-white">{workflow.name}</h3>
            <div className="flex items-center gap-2 mt-1">
              <StatusBadge status={workflow.status} />
              <span className="text-xs text-slate-500">
                Created {new Date(workflow.created_at).toLocaleString()}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <Icon name="close" className="text-xl" />
          </button>
        </div>

        {/* Steps list */}
        <div className="flex-1 overflow-y-auto space-y-2 mb-4">
          {workflow.steps.map((step, i) => (
            <div
              key={step.id}
              className="flex items-start gap-3 p-3 rounded-lg bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50"
            >
              <div className="mt-0.5">
                <StepIcon status={step.status} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">{step.agent_name}</span>
                  <StatusBadge status={step.status} />
                  {step.depends_on.length > 0 && (
                    <span className="text-xs text-slate-500">
                      after {step.depends_on.join(', ')}
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 truncate">{step.prompt}</p>
                {step.error && (
                  <p className="text-xs text-red-600 dark:text-red-400 mt-1">{step.error}</p>
                )}
                <div className="flex gap-3 mt-1 text-[10px] text-slate-600">
                  <span>{step.model} | ${step.budget}</span>
                  {step.started_at && (
                    <span>Started {new Date(step.started_at).toLocaleTimeString()}</span>
                  )}
                  {step.finished_at && (
                    <span>Finished {new Date(step.finished_at).toLocaleTimeString()}</span>
                  )}
                </div>
              </div>
              <span className="text-[10px] text-slate-600 shrink-0">Step {i + 1}</span>
            </div>
          ))}
        </div>

        {/* Actions */}
        <div className="flex gap-3 justify-end">
          <button
            onClick={() => onDelete(workflow.id)}
            className="text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 text-sm px-4 py-2 transition-colors"
          >
            Delete
          </button>
          <button
            onClick={() => onRun(workflow.id)}
            disabled={workflow.status === 'running'}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-200 dark:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            {workflow.status === 'running' ? 'Running...' : 'Run automation'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// New workflow form
// ---------------------------------------------------------------------------

interface DraftStep {
  agent_name: string
  prompt: string
  model: string
  budget: number
  depends_on: string[]
}

function NewWorkflowModal({
  onSave,
  onCancel,
}: {
  onSave: (name: string, steps: DraftStep[]) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [steps, setSteps] = useState<DraftStep[]>([
    { agent_name: '', prompt: '', model: 'sonnet', budget: 2.0, depends_on: [] },
  ])

  // Escape cancels the new workflow modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const addStep = () => {
    setSteps((prev) => [
      ...prev,
      { agent_name: '', prompt: '', model: 'sonnet', budget: 2.0, depends_on: [] },
    ])
  }

  const removeStep = (i: number) => {
    setSteps((prev) => prev.filter((_, idx) => idx !== i))
  }

  const updateStep = (i: number, field: keyof DraftStep, value: string | number | string[]) => {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, [field]: value } : s)))
  }

  const valid = name.trim() && steps.every((s) => s.agent_name.trim() && s.prompt.trim())

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-2xl w-full max-w-2xl p-6 shadow-xl max-h-[85vh] flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">New automation</h3>

        {/* Name */}
        <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Automation name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Research and write report"
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-5"
        />

        {/* Steps */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-slate-600 dark:text-slate-400 font-medium">Steps</span>
          <button
            onClick={addStep}
            className="flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
          >
            <Icon name="add" className="text-base" />
            Add step
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-3 mb-5">
          {steps.map((step, i) => {
            const prevStepIds = steps.slice(0, i).map((_, idx) => `step-${idx + 1}`)
            return (
              <div
                key={i}
                className="p-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg space-y-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-400">Step {i + 1}</span>
                  {steps.length > 1 && (
                    <button
                      onClick={() => removeStep(i)}
                      className="text-slate-600 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                    >
                      <Icon name="delete" className="text-sm" />
                    </button>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] text-slate-500">Agent name</label>
                    <input
                      type="text"
                      value={step.agent_name}
                      onChange={(e) => updateStep(i, 'agent_name', e.target.value)}
                      placeholder="e.g. researcher"
                      className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded px-2 py-1.5 text-white text-xs placeholder-slate-600 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[11px] text-slate-500">Model</label>
                      <select
                        value={step.model}
                        onChange={(e) => updateStep(i, 'model', e.target.value)}
                        className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded px-2 py-1.5 text-white text-xs focus:outline-none focus:border-blue-500"
                      >
                        <option value="sonnet">Sonnet</option>
                        <option value="opus">Opus</option>
                        <option value="haiku">Haiku</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[11px] text-slate-500">Budget ($)</label>
                      <input
                        type="number"
                        min={0}
                        step={0.5}
                        value={step.budget}
                        onChange={(e) => updateStep(i, 'budget', parseFloat(e.target.value) || 0)}
                        className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded px-2 py-1.5 text-white text-xs focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                </div>

                <div>
                  <label className="text-[11px] text-slate-500">What should this agent do?</label>
                  <textarea
                    value={step.prompt}
                    onChange={(e) => updateStep(i, 'prompt', e.target.value)}
                    rows={2}
                    placeholder="Describe what this agent should do..."
                    className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded px-2 py-1.5 text-white text-xs placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
                  />
                </div>

                {prevStepIds.length > 0 && (
                  <div>
                    <label className="text-[11px] text-slate-500">
                      Wait for (leave blank to run at the same time)
                    </label>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {prevStepIds.map((pid) => {
                        const checked = step.depends_on.includes(pid)
                        return (
                          <button
                            key={pid}
                            onClick={() => {
                              const next = checked
                                ? step.depends_on.filter((d) => d !== pid)
                                : [...step.depends_on, pid]
                              updateStep(i, 'depends_on', next)
                            }}
                            className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                              checked
                                ? 'border-blue-500 text-blue-600 dark:text-blue-400 bg-blue-500/10'
                                : 'border-slate-200 dark:border-slate-700 text-slate-500 hover:border-slate-500'
                            }`}
                          >
                            {pid}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-3">
          <button onClick={onCancel} className="text-slate-600 dark:text-slate-400 hover:text-white text-sm px-4 py-2 transition-colors">
            Cancel
          </button>
          <button
            onClick={() => { if (valid) onSave(name, steps) }}
            disabled={!valid}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-200 dark:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            Save automation
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Automation templates
// ---------------------------------------------------------------------------

interface AutomationTemplate {
  id: string
  name: string
  description: string
  icon: string
  steps: { name: string; prompt: string }[]
}

// ---------------------------------------------------------------------------
// Main Workflows page
// ---------------------------------------------------------------------------

export default function Workflows() {
  const navigate = useNavigate()
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Workflow | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [templates, setTemplates] = useState<AutomationTemplate[]>([])
  const [expandedRunsId, setExpandedRunsId] = useState<string | null>(null)

  const fetchWorkflows = useCallback(async () => {
    try {
      const res = await api.get<{ workflows: Workflow[] }>('/workflows')
      setWorkflows(res.workflows)
    } catch {
      // ignore fetch errors silently
    } finally {
      setLoading(false)
    }
  }, [])

  // Fetch built-in templates once
  useEffect(() => {
    const fetchTemplates = async () => {
      try {
        const res = await api.get<{ templates: AutomationTemplate[] }>('/workflows/templates')
        setTemplates(res.templates)
      } catch {
        // ignore, templates are non-critical
      }
    }
    fetchTemplates()
  }, [])

  useEffect(() => {
    fetchWorkflows()
    const interval = setInterval(fetchWorkflows, 3000)
    return () => clearInterval(interval)
  }, [fetchWorkflows])

  // Real-time fast path: subscribe to /ws/workflows/state and push updates
  // into the store. The HTTP poll above remains as a safety net when WS drops.
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/ws/workflows/state`)
    ws.onmessage = (evt: MessageEvent) => {
      try {
        const frame = JSON.parse(evt.data as string) as { type: string; workflows?: Workflow[] }
        if ((frame.type === 'snapshot' || frame.type === 'delta') && Array.isArray(frame.workflows)) {
          useWorkflowsStore.getState().setWorkflows(frame.workflows)
        }
      } catch {
        // ignore malformed frames
      }
    }
    return () => ws.close()
  }, [])

  // Merge WS store data into local state whenever the store updates.
  // Fires within one WS delta (~50 ms) instead of waiting up to 3 s for the
  // HTTP poll. The poll keeps running as a safety net.
  const storeWorkflows = useWorkflowsStore((s) => s.workflows)
  useEffect(() => {
    if (storeWorkflows.length === 0) return
    setWorkflows(storeWorkflows as Workflow[])
  }, [storeWorkflows])

  // Keep selected workflow in sync with polled data
  useEffect(() => {
    if (selected) {
      const updated = workflows.find((w) => w.id === selected.id)
      if (updated) setSelected(updated)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflows])

  const handleCreate = async (name: string, steps: DraftStep[]) => {
    setError('')
    try {
      const res = await api.post<{ workflow: Workflow }>('/workflows', { name, steps })
      setWorkflows((prev) => [res.workflow, ...prev])
      setShowNew(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create workflow')
    }
  }

  const handleRun = async (id: string) => {
    setError('')
    setStatus('')
    // Optimistic update so the user sees immediate feedback instead of a
    // silent click. The 3 second polling loop will overwrite this with the
    // real state as soon as the backend reports it.
    setWorkflows((prev) =>
      prev.map((w) => (w.id === id ? { ...w, status: 'running' as const } : w)),
    )
    try {
      await api.post(`/workflows/${id}/run`)
      setStatus('Automation started. Steps will update as they finish.')
      setTimeout(() => setStatus(''), 5000)
      await fetchWorkflows()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to start workflow')
      // Revert the optimistic update so the card does not appear stuck.
      await fetchWorkflows()
    }
  }

  const handleDelete = async (id: string) => {
    setError('')
    try {
      await api.delete(`/workflows/${id}`)
      setWorkflows((prev) => prev.filter((w) => w.id !== id))
      setSelected(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete workflow')
    }
  }

  const handleUseTemplate = (tpl: AutomationTemplate) => {
    // Pre-fill the builder via localStorage and navigate to it.
    // Slugify the step name so the spawned agent gets a clean identifier
    // like "closed-this-week" instead of the display label "Closed this week".
    const slugify = (s: string) =>
      s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
    const prefill = {
      name: tpl.name,
      steps: tpl.steps.map((s) => ({
        agent_name: slugify(s.name),
        prompt: s.prompt,
        model: 'sonnet',
        budget: 2.0,
        depends_on: [],
      })),
    }
    try {
      localStorage.setItem('automation-template-prefill', JSON.stringify(prefill))
    } catch {
      // ignore storage failures
    }
    navigate('/workflows/builder')
  }

  return (
    <div className="min-h-dvh bg-white dark:bg-slate-950 text-slate-900 dark:text-white">
      <TopBar title="Automations" />

      <div className="px-4 pb-4 sm:px-8 sm:pb-8">
        {/* Header row */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-white">Automations</h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              Chain agents together. Steps run at the same time unless you set dependencies.
            </p>
          </div>
          <Button
            variant="primary"
            size="md"
            onClick={() => navigate('/workflows/builder')}
          >
            <Icon name="add" className="text-base" />
            New automation
          </Button>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorBanner message={error} />
          </div>
        )}

        {status && (
          <div
            role="status"
            className="mb-4 p-3 rounded-lg bg-green-500/10 border border-green-500/30 text-green-600 dark:text-green-400 text-sm"
          >
            {status}
          </div>
        )}

        {/* Templates section */}
        {templates.length > 0 && (
          <div className="mb-8">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-2">Templates</h2>
            <p className="text-xs text-slate-500 mb-3">
              Start from a ready-made automation. Click Use to open it in the builder.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  className="flex items-start gap-3 p-4 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 hover:border-slate-600 transition-colors"
                >
                  <Icon name={tpl.icon} className="text-2xl text-pink-600 dark:text-pink-400 shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-white">{tpl.name}</span>
                      <span className="text-[10px] text-slate-500">
                        {tpl.steps.length} {tpl.steps.length === 1 ? 'step' : 'steps'}
                      </span>
                    </div>
                    <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">{tpl.description}</p>
                  </div>
                  <button
                    onClick={() => handleUseTemplate(tpl)}
                    className="shrink-0 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-900 dark:text-slate-100 text-xs font-medium rounded-lg px-3 py-1.5 transition-colors"
                  >
                    Use
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Workflow list */}
        {loading ? (
          <div className="text-slate-500 text-sm">Loading...</div>
        ) : workflows.length === 0 ? (
          <EmptyState
            icon="account_tree"
            title="No automations yet"
            description="Create an automation to run multiple agents in a sequence or at the same time."
            action={{ label: "New automation", onClick: () => navigate('/workflows/builder') }}
          />
        ) : (
          <div className="space-y-3">
            {workflows.map((wf) => {
              const runsOpen = expandedRunsId === wf.id
              return (
                <Card
                  key={wf.id}
                  variant="elevated"
                  padding="sm"
                  hover
                  className="overflow-hidden"
                >
                  {/* Main row */}
                  <div
                    onClick={() => setSelected(wf)}
                    className="flex items-center gap-4 p-4 cursor-pointer"
                  >
                    <Icon
                      name="account_tree"
                      className={`text-2xl shrink-0 ${
                        wf.status === 'running'
                          ? 'text-blue-600 dark:text-blue-400'
                          : wf.status === 'done'
                          ? 'text-green-600 dark:text-green-400'
                          : wf.status === 'failed'
                          ? 'text-red-600 dark:text-red-400'
                          : 'text-slate-600'
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-white">{wf.name}</span>
                        <StatusBadge status={wf.status} />
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5">
                        {wf.steps.length} {wf.steps.length === 1 ? 'step' : 'steps'} &middot;{' '}
                        {new Date(wf.created_at).toLocaleString()}
                      </p>
                    </div>

                    {/* Step progress dots */}
                    <div className="flex gap-1 shrink-0">
                      {wf.steps.map((s) => (
                        <span
                          key={s.id}
                          title={`${s.agent_name}: ${s.status}`}
                          className={`w-2 h-2 rounded-full ${
                            s.status === 'done'
                              ? 'bg-green-400'
                              : s.status === 'running'
                              ? 'bg-blue-400 animate-pulse'
                              : s.status === 'failed'
                              ? 'bg-red-400'
                              : s.status === 'skipped'
                              ? 'bg-yellow-400'
                              : 'bg-slate-200 dark:bg-slate-700'
                          }`}
                        />
                      ))}
                    </div>

                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRun(wf.id) }}
                        disabled={wf.status === 'running'}
                        title="Run automation"
                        className="p-1.5 rounded-lg text-slate-600 dark:text-slate-400 hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800 disabled:text-slate-700 transition-colors"
                      >
                        <Icon name="play_arrow" className="text-base" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setExpandedRunsId(runsOpen ? null : wf.id) }}
                        title="Show run history"
                        className={`p-1.5 rounded-lg transition-colors ${
                          runsOpen
                            ? 'text-indigo-600 dark:text-indigo-400 bg-indigo-500/10'
                            : 'text-slate-600 dark:text-slate-400 hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800'
                        }`}
                      >
                        <Icon name="history" className="text-base" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); navigate(`/workflows/builder/${wf.id}`) }}
                        title="Edit automation"
                        className="p-1.5 rounded-lg text-slate-600 dark:text-slate-400 hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                      >
                        <Icon name="edit" className="text-base" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(wf.id) }}
                        title="Delete automation"
                        className="p-1.5 rounded-lg text-slate-600 dark:text-slate-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                      >
                        <Icon name="delete" className="text-base" />
                      </button>
                    </div>
                  </div>

                  {/* Run history panel */}
                  {runsOpen && (
                    <div className="border-t border-slate-200 dark:border-slate-800">
                      <RunsDrawer workflowId={wf.id} />
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        )}
      </div>

      {/* Modals */}
      {showNew && (
        <NewWorkflowModal onSave={handleCreate} onCancel={() => setShowNew(false)} />
      )}
      {selected && (
        <WorkflowDetail
          workflow={selected}
          onClose={() => setSelected(null)}
          onRun={handleRun}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}
