import { useState, useEffect, useCallback } from 'react'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import { api } from '../lib/api'

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

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; label: string }> = {
    pending: { color: 'text-slate-400 bg-slate-800', label: 'Waiting' },
    running: { color: 'text-blue-400 bg-blue-500/20', label: 'Running' },
    done: { color: 'text-green-400 bg-green-500/20', label: 'Done' },
    failed: { color: 'text-red-400 bg-red-500/20', label: 'Failed' },
    skipped: { color: 'text-yellow-400 bg-yellow-500/20', label: 'Skipped' },
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
  if (status === 'done') return <Icon name="check_circle" className="text-green-400 text-lg" />
  if (status === 'running') return <Icon name="pending" className="text-blue-400 text-lg animate-pulse" />
  if (status === 'failed') return <Icon name="cancel" className="text-red-400 text-lg" />
  if (status === 'skipped') return <Icon name="remove_circle" className="text-yellow-400 text-lg" />
  return <Icon name="radio_button_unchecked" className="text-slate-600 text-lg" />
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
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-2xl p-6 shadow-xl max-h-[80vh] flex flex-col">
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
              className="flex items-start gap-3 p-3 rounded-lg bg-slate-800/50 border border-slate-700/50"
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
                <p className="text-xs text-slate-400 mt-0.5 truncate">{step.prompt}</p>
                {step.error && (
                  <p className="text-xs text-red-400 mt-1">{step.error}</p>
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
            className="text-red-400 hover:text-red-300 text-sm px-4 py-2 transition-colors"
          >
            Delete
          </button>
          <button
            onClick={() => onRun(workflow.id)}
            disabled={workflow.status === 'running'}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            {workflow.status === 'running' ? 'Running...' : 'Run workflow'}
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
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-2xl p-6 shadow-xl max-h-[85vh] flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">New workflow</h3>

        {/* Name */}
        <label className="block text-sm text-slate-400 mb-1">Workflow name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Research and write report"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-5"
        />

        {/* Steps */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-slate-400 font-medium">Steps</span>
          <button
            onClick={addStep}
            className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
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
                className="p-3 bg-slate-800/50 border border-slate-700/50 rounded-lg space-y-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-400">Step {i + 1}</span>
                  {steps.length > 1 && (
                    <button
                      onClick={() => removeStep(i)}
                      className="text-slate-600 hover:text-red-400 transition-colors"
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
                      className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-white text-xs placeholder-slate-600 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[11px] text-slate-500">Model</label>
                      <select
                        value={step.model}
                        onChange={(e) => updateStep(i, 'model', e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-white text-xs focus:outline-none focus:border-blue-500"
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
                        className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-white text-xs focus:outline-none focus:border-blue-500"
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
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-white text-xs placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
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
                                ? 'border-blue-500 text-blue-400 bg-blue-500/10'
                                : 'border-slate-700 text-slate-500 hover:border-slate-500'
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
          <button onClick={onCancel} className="text-slate-400 hover:text-white text-sm px-4 py-2 transition-colors">
            Cancel
          </button>
          <button
            onClick={() => { if (valid) onSave(name, steps) }}
            disabled={!valid}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            Save workflow
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Workflows page
// ---------------------------------------------------------------------------

export default function Workflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Workflow | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [error, setError] = useState('')

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

  useEffect(() => {
    fetchWorkflows()
    const interval = setInterval(fetchWorkflows, 3000)
    return () => clearInterval(interval)
  }, [fetchWorkflows])

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
    try {
      await api.post(`/workflows/${id}/run`)
      await fetchWorkflows()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to start workflow')
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

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Workflows" />

      <div className="pt-20 p-8">
        {/* Header row */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Workflows</h1>
            <p className="text-sm text-slate-400 mt-1">
              Chain agents together. Steps run at the same time unless you set dependencies.
            </p>
          </div>
          <button
            onClick={() => setShowNew(true)}
            className="flex items-center gap-2 bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            <Icon name="add" className="text-base" />
            New workflow
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Workflow list */}
        {loading ? (
          <div className="text-slate-500 text-sm">Loading...</div>
        ) : workflows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Icon name="account_tree" className="text-5xl text-slate-700 mb-4" />
            <p className="text-slate-400 text-lg font-medium">No workflows yet</p>
            <p className="text-slate-600 text-sm mt-1">
              Create a workflow to run multiple agents in a sequence or at the same time.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {workflows.map((wf) => (
              <div
                key={wf.id}
                onClick={() => setSelected(wf)}
                className="flex items-center gap-4 p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-600 cursor-pointer transition-colors"
              >
                <Icon
                  name="account_tree"
                  className={`text-2xl shrink-0 ${
                    wf.status === 'running'
                      ? 'text-blue-400'
                      : wf.status === 'done'
                      ? 'text-green-400'
                      : wf.status === 'failed'
                      ? 'text-red-400'
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
                          : 'bg-slate-700'
                      }`}
                    />
                  ))}
                </div>

                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleRun(wf.id) }}
                    disabled={wf.status === 'running'}
                    title="Run workflow"
                    className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 disabled:text-slate-700 transition-colors"
                  >
                    <Icon name="play_arrow" className="text-base" />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(wf.id) }}
                    title="Delete workflow"
                    className="p-1.5 rounded-lg text-slate-400 hover:text-red-400 hover:bg-slate-800 transition-colors"
                  >
                    <Icon name="delete" className="text-base" />
                  </button>
                </div>
              </div>
            ))}
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
