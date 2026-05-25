import { useState, useEffect, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";
import { Button, LoadingState } from "../components/ui";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StepDraft {
  id: string;
  agent_name: string;
  prompt: string;
  model: string;
  budget: number;
  depends_on: string[];
  // runtime status (only populated when workflow is running)
  status?: string;
  error?: string;
}

interface WorkflowStatus {
  id: string;
  name: string;
  status: string;
  steps: StepDraft[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MODELS = [
  { value: "sonnet", label: "Sonnet (default)" },
  { value: "haiku", label: "Haiku (faster)" },
  { value: "opus", label: "Opus (most capable)" },
];

const stepStatusStyles: Record<string, string> = {
  pending: "bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-400",
  running: "bg-blue-500/20 text-blue-700 dark:text-blue-300 animate-pulse",
  done: "bg-green-500/20 text-green-600 dark:text-green-400",
  failed: "bg-red-500/20 text-red-600 dark:text-red-400",
  skipped: "bg-slate-600/50 text-slate-500",
};

// ---------------------------------------------------------------------------
// Unique ID helper
// ---------------------------------------------------------------------------

let _counter = 0;
function newStepId(): string {
  _counter += 1;
  return `step-${Date.now()}-${_counter}`;
}

function emptyStep(): StepDraft {
  return {
    id: newStepId(),
    agent_name: "",
    prompt: "",
    model: "sonnet",
    budget: 2.0,
    depends_on: [],
  };
}

// ---------------------------------------------------------------------------
// Sortable step card
// ---------------------------------------------------------------------------

function StepCard({
  step,
  index,
  allSteps,
  onChange,
  onRemove,
  isOnly,
}: {
  step: StepDraft;
  index: number;
  allSteps: StepDraft[];
  onChange: (updated: StepDraft) => void;
  onRemove: () => void;
  isOnly: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: step.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const prevSteps = allSteps.slice(0, index);

  const toggleDep = (depId: string) => {
    const current = step.depends_on;
    if (current.includes(depId)) {
      onChange({ ...step, depends_on: current.filter((d) => d !== depId) });
    } else {
      onChange({ ...step, depends_on: [...current, depId] });
    }
  };

  const statusLabel = step.status && step.status !== "pending" ? step.status : null;

  return (
    <div ref={setNodeRef} style={style} className="relative">
      {/* Connector line from previous step */}
      {index > 0 && (
        <div className="flex justify-center -mt-0.5 mb-0.5">
          <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />
        </div>
      )}

      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5 flex flex-col gap-4">
        {/* Card header */}
        <div className="flex items-center gap-3">
          <button
            {...attributes}
            {...listeners}
            className="text-slate-600 hover:text-slate-600 dark:hover:text-slate-400 cursor-grab active:cursor-grabbing touch-none"
            aria-label="Drag to reorder"
          >
            <Icon name="drag_indicator" className="text-xl" />
          </button>

          <span className="flex items-center justify-center w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 text-xs font-bold shrink-0">
            {index + 1}
          </span>

          <input
            type="text"
            value={step.agent_name}
            onChange={(e) => onChange({ ...step, agent_name: e.target.value })}
            placeholder={`Step ${index + 1} name`}
            className="flex-1 bg-transparent text-slate-900 dark:text-slate-100 font-medium placeholder-slate-600 outline-none border-b border-transparent focus:border-slate-600 transition-colors"
          />

          {statusLabel && (
            <span
              className={`px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${
                stepStatusStyles[statusLabel] ?? "bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-400"
              }`}
            >
              {statusLabel}
            </span>
          )}

          {!isOnly && (
            <button
              onClick={onRemove}
              className="text-slate-600 hover:text-red-600 dark:hover:text-red-400 transition-colors"
              aria-label="Remove step"
            >
              <Icon name="close" className="text-lg" />
            </button>
          )}
        </div>

        {/* Prompt */}
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">What should this step do?</label>
          <textarea
            value={step.prompt}
            onChange={(e) => onChange({ ...step, prompt: e.target.value })}
            placeholder="Describe the task for this step..."
            rows={3}
            className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder-slate-600 outline-none focus:border-slate-500 resize-none transition-colors"
          />
        </div>

        {/* Model + Budget row */}
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs text-slate-500 mb-1.5">Model</label>
            <select
              value={step.model}
              onChange={(e) => onChange({ ...step, model: e.target.value })}
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-slate-100 outline-none focus:border-slate-500 transition-colors"
            >
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <label className="block text-xs text-slate-500 mb-1.5">Max spend ($)</label>
            <input
              type="number"
              min={0.1}
              max={100}
              step={0.5}
              value={step.budget}
              onChange={(e) => onChange({ ...step, budget: parseFloat(e.target.value) || 2.0 })}
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-slate-100 outline-none focus:border-slate-500 transition-colors"
            />
          </div>
        </div>

        {/* Depends on */}
        {prevSteps.length > 0 && (
          <div>
            <label className="block text-xs text-slate-500 mb-2">
              Wait for these steps to finish first:
            </label>
            <div className="flex flex-wrap gap-2">
              {prevSteps.map((prev, pi) => (
                <button
                  key={prev.id}
                  onClick={() => toggleDep(prev.id)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors ${
                    step.depends_on.includes(prev.id)
                      ? "bg-indigo-500/20 border-indigo-500/40 text-indigo-700 dark:text-indigo-300"
                      : "bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-500"
                  }`}
                >
                  {step.depends_on.includes(prev.id) ? (
                    <Icon name="check_circle" className="text-sm" />
                  ) : (
                    <Icon name="radio_button_unchecked" className="text-sm" />
                  )}
                  Step {pi + 1}{prev.agent_name ? `: ${prev.agent_name}` : ""}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Error message */}
        {step.error && (
          <p className="text-xs text-red-600 dark:text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{step.error}</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function WorkflowBuilder() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();

  const [name, setName] = useState("My automation");
  const [steps, setSteps] = useState<StepDraft[]>([emptyStep()]);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [polling, setPolling] = useState(false);
  const [workflowId, setWorkflowId] = useState<string | null>(id ?? null);
  const [workflowStatus, setWorkflowStatus] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"success" | "error">("success");

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  // Load existing workflow if editing
  useEffect(() => {
    if (!id) {
      // Check for a template prefill left by the Automations page
      try {
        const raw = localStorage.getItem("automation-template-prefill");
        if (raw) {
          const prefill = JSON.parse(raw) as {
            name: string;
            steps: {
              agent_name: string;
              prompt: string;
              model?: string;
              budget?: number;
              depends_on?: string[];
            }[];
          };
          if (prefill?.name && Array.isArray(prefill.steps) && prefill.steps.length > 0) {
            setName(prefill.name);
            setSteps(
              prefill.steps.map((s) => ({
                id: newStepId(),
                agent_name: s.agent_name,
                prompt: s.prompt,
                model: s.model || "sonnet",
                budget: s.budget ?? 2.0,
                depends_on: s.depends_on || [],
              }))
            );
          }
          localStorage.removeItem("automation-template-prefill");
        }
      } catch {
        // ignore bad prefill payloads
      }
      return;
    }
    const load = async () => {
      try {
        const data = await api.get<{ workflow: WorkflowStatus }>(`/workflows/${id}/status`);
        const wf = data.workflow;
        setName(wf.name);
        setSteps(
          wf.steps.map((s) => ({
            id: s.id || newStepId(),
            agent_name: s.agent_name,
            prompt: s.prompt,
            model: s.model || "sonnet",
            budget: s.budget ?? 2.0,
            depends_on: s.depends_on || [],
            status: s.status,
          }))
        );
        setWorkflowStatus(wf.status);
      } catch {
        showMessage("Could not load automation. Check that the backend is running and try again.", "error");
      }
    };
    load();
  }, [id]);

  // Poll status while running
  const pollStatus = useCallback(async () => {
    const wfId = workflowId;
    if (!wfId) return;
    try {
      const data = await api.get<{ workflow: WorkflowStatus }>(`/workflows/${wfId}/status`);
      const wf = data.workflow;
      setWorkflowStatus(wf.status);
      setSteps((prev) =>
        prev.map((s) => {
          const live = wf.steps.find((ls) => ls.id === s.id);
          return live ? { ...s, status: live.status, error: live.error } : s;
        })
      );
      if (wf.status !== "running") {
        setPolling(false);
        setRunning(false);
        if (wf.status === "done") showMessage("Automation finished.");
        if (wf.status === "failed") showMessage("One or more steps failed.", "error");
      }
    } catch {
      setPolling(false);
      setRunning(false);
    }
  }, [workflowId]);

  useEffect(() => {
    if (!polling) return;
    const interval = setInterval(pollStatus, 3000);
    return () => clearInterval(interval);
  }, [polling, pollStatus]);

  const showMessage = (text: string, type: "success" | "error" = "success") => {
    setMessage(text);
    setMessageType(type);
    setTimeout(() => setMessage(""), 5000);
  };

  // Drag reorder
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setSteps((prev) => {
      const oldIndex = prev.findIndex((s) => s.id === active.id);
      const newIndex = prev.findIndex((s) => s.id === over.id);
      return arrayMove(prev, oldIndex, newIndex);
    });
  };

  const updateStep = (index: number, updated: StepDraft) => {
    setSteps((prev) => prev.map((s, i) => (i === index ? updated : s)));
  };

  const removeStep = (index: number) => {
    setSteps((prev) => {
      const removed = prev[index];
      return prev
        .filter((_, i) => i !== index)
        .map((s) => ({
          ...s,
          depends_on: s.depends_on.filter((d) => d !== removed.id),
        }));
    });
  };

  const addStep = () => {
    setSteps((prev) => [...prev, emptyStep()]);
  };

  // Save
  const handleSave = async () => {
    if (!name.trim()) {
      showMessage("Give your automation a name first.", "error");
      return;
    }
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        steps: steps.map((s) => ({
          id: s.id,
          agent_name: s.agent_name || `step-${steps.indexOf(s) + 1}`,
          prompt: s.prompt,
          model: s.model,
          budget: s.budget,
          depends_on: s.depends_on,
        })),
      };

      if (workflowId) {
        await api.put(`/workflows/${workflowId}`, body);
        showMessage("Automation saved.");
      } else {
        const data = await api.post<{ workflow: { id: string } }>("/workflows", body);
        const newId = data.workflow.id;
        setWorkflowId(newId);
        navigate(`/workflows/builder/${newId}`, { replace: true });
        showMessage("Automation saved.");
      }
    } catch {
      showMessage("Could not save automation. Check your connection and try again.", "error");
    } finally {
      setSaving(false);
    }
  };

  // Run
  const handleRun = async () => {
    let wfId = workflowId;

    // Auto-save first if needed
    if (!wfId) {
      if (!name.trim()) {
        showMessage("Give your automation a name first.", "error");
        return;
      }
      try {
        setSaving(true);
        const body = {
          name: name.trim(),
          steps: steps.map((s, i) => ({
            id: s.id,
            agent_name: s.agent_name || `step-${i + 1}`,
            prompt: s.prompt,
            model: s.model,
            budget: s.budget,
            depends_on: s.depends_on,
          })),
        };
        const data = await api.post<{ workflow: { id: string } }>("/workflows", body);
        wfId = data.workflow.id;
        setWorkflowId(wfId);
        navigate(`/workflows/builder/${wfId}`, { replace: true });
      } catch {
        showMessage("Could not save automation. Check your connection and try again.", "error");
        setSaving(false);
        return;
      } finally {
        setSaving(false);
      }
    }

    try {
      setRunning(true);
      await api.post(`/workflows/${wfId}/run`);
      setWorkflowStatus("running");
      setPolling(true);
      showMessage("Automation started.");
    } catch {
      setRunning(false);
      showMessage("Could not start automation. Check your connection and try again.", "error");
    }
  };

  const isRunning = workflowStatus === "running" || running;

  return (
    <div className="min-h-dvh bg-white dark:bg-slate-950 text-white">
      <TopBar title={id ? "Edit automation" : "New automation"} />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        <div className="max-w-2xl mx-auto">
          {/* Header */}
          <div className="flex items-start justify-between gap-4 mb-6">
            <div className="flex-1">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Automation name"
                className="w-full bg-transparent text-2xl font-bold text-slate-900 dark:text-slate-100 placeholder-slate-600 outline-none border-b border-transparent focus:border-slate-600 transition-colors pb-1"
              />
              <p className="text-sm text-slate-500 mt-1">
                Add steps below. Each step runs an agent with your instructions.
              </p>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="secondary"
                size="md"
                onClick={handleSave}
                disabled={saving || isRunning}
                loading={saving}
              >
                <Icon name="save" className="text-base" />
                {saving ? "Saving..." : "Save"}
              </Button>
              <Button
                variant="primary"
                size="md"
                onClick={handleRun}
                disabled={isRunning || saving}
                loading={isRunning}
              >
                <Icon name="play_arrow" className="text-base" />
                {isRunning ? "Running..." : "Run automation"}
              </Button>
            </div>
          </div>

          {/* Workflow status banner */}
          {workflowStatus && workflowStatus !== "pending" && (
            workflowStatus === "running" ? (
              <div className="mb-4" data-testid="workflow-loading-state">
                <LoadingState variant="spinner" message="Automation is running..." />
              </div>
            ) : (
              <div
                className={`mb-4 px-4 py-3 rounded-lg text-sm font-medium flex items-center gap-2 ${
                  workflowStatus === "done"
                    ? "bg-green-500/10 text-green-600 dark:text-green-400"
                    : workflowStatus === "failed"
                    ? "bg-red-500/10 text-red-600 dark:text-red-400"
                    : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400"
                }`}
              >
                {workflowStatus === "done" && <Icon name="check_circle" className="text-base" />}
                {workflowStatus === "failed" && <Icon name="error" className="text-base" />}
                <span>
                  {workflowStatus === "done" && "Automation finished successfully."}
                  {workflowStatus === "failed" && "Automation failed. Check steps below."}
                </span>
              </div>
            )
          )}

          {/* Message */}
          {message && (
            <div
              className={`mb-4 px-4 py-3 rounded-lg text-sm font-medium ${
                messageType === "success"
                  ? "bg-green-500/20 text-green-600 dark:text-green-400"
                  : "bg-red-500/20 text-red-600 dark:text-red-400"
              }`}
            >
              {message}
            </div>
          )}

          {/* Steps */}
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext items={steps.map((s) => s.id)} strategy={verticalListSortingStrategy}>
              <div className="flex flex-col">
                {steps.map((step, index) => (
                  <StepCard
                    key={step.id}
                    step={step}
                    index={index}
                    allSteps={steps}
                    onChange={(updated) => updateStep(index, updated)}
                    onRemove={() => removeStep(index)}
                    isOnly={steps.length === 1}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>

          {/* Add step */}
          <div className="flex justify-center mt-4">
            <div className="flex items-center gap-2">
              <div className="w-px h-4 bg-slate-200 dark:bg-slate-700" />
            </div>
          </div>
          <div className="flex justify-center mt-1">
            <button
              onClick={addStep}
              className="flex items-center gap-2 px-5 py-2.5 border border-dashed border-slate-200 dark:border-slate-700 hover:border-indigo-500 text-slate-600 dark:text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 text-sm font-medium rounded-xl transition-colors"
            >
              <Icon name="add" className="text-base" />
              Add step
            </button>
          </div>

          {/* Back link */}
          <div className="mt-8 flex justify-center">
            <button
              onClick={() => navigate("/workflows")}
              className="text-xs text-slate-600 hover:text-slate-600 dark:hover:text-slate-400 transition-colors"
            >
              Back to all automations
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
