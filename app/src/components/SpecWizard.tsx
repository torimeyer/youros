import { useState } from "react";
import { Button } from "./ui";
import Icon from "./Icon";
import { api } from "../lib/api";

interface Props {
  onComplete: (path: string) => void;
  onCancel: () => void;
}

interface Suggestions {
  criteria: string[];
  non_goals: string[];
  after_statement: string;
}

type Mode = "task" | "spec";

const SPEC_STEPS = ["Type", "Problem", "Scope", "Produces", "Criteria", "Review"] as const;
type SpecStep = typeof SPEC_STEPS[number];

type SpecType = "engineering" | "prototype" | "vision" | "customer_docs";

const SPEC_TYPE_OPTIONS: { value: SpecType; label: string; description: string; icon: string }[] = [
  { value: "engineering", label: "An engineering feature", description: "Code and tasks a builder can ship, with success criteria", icon: "code" },
  { value: "prototype", label: "A quick prototype", description: "A spike to learn something fast, light on process", icon: "science" },
  { value: "vision", label: "A vision or roadmap", description: "Where this is headed and how you'll know it's working", icon: "explore" },
  { value: "customer_docs", label: "Customer-facing docs", description: "A guide or explainer written for a specific audience", icon: "menu_book" },
];

type ProducesOption = "code" | "agent" | "document" | "slides" | "diagram" | "skill";

const PRODUCES_OPTIONS: { value: ProducesOption; label: string; description: string; icon: string }[] = [
  { value: "code", label: "Code", description: "Breaks into tasks, builds files and features", icon: "code" },
  { value: "agent", label: "Agent", description: "Writes a new agent to your agent library", icon: "smart_toy" },
  { value: "document", label: "Document", description: "Drafts a .docx and saves it to Drive", icon: "description" },
  { value: "slides", label: "Slide deck", description: "Builds a .pptx and saves it to Drive", icon: "slideshow" },
  { value: "diagram", label: "Diagram", description: "Draws a .drawio and saves it to Drive", icon: "account_tree" },
  { value: "skill", label: "Skill", description: "Writes a reusable skill to your skill library", icon: "extension" },
];

export default function SpecWizard({ onComplete, onCancel }: Props) {
  const [mode, setMode] = useState<Mode>("task");

  // Task fields
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  const [saving, setSaving] = useState(false);

  // Spec fields
  const [step, setStep] = useState<SpecStep>("Type");
  const [specType, setSpecType] = useState<SpecType>("engineering");
  const [title, setTitle] = useState("");
  const [problem, setProblem] = useState("");
  const [afterStatement, setAfterStatement] = useState("");
  const [inScope, setInScope] = useState<string[]>([]);
  const [outOfScope, setOutOfScope] = useState<string[]>([]);
  const [nonGoals, setNonGoals] = useState<string[]>([]);
  const [criteria, setCriteria] = useState<string[]>([]);
  const [newScopeItem, setNewScopeItem] = useState("");
  const [newOutItem, setNewOutItem] = useState("");
  const [newNonGoal, setNewNonGoal] = useState("");
  const [newCriterion, setNewCriterion] = useState("");
  const [produces, setProduces] = useState<ProducesOption>("code");
  const [suggesting, setSuggesting] = useState(false);
  const [creating, setCreating] = useState(false);

  const stepIndex = SPEC_STEPS.indexOf(step);

  // Per-type prompts keep the wizard guided: a vision or docs spec is not asked
  // for engineering-style acceptance criteria.
  const problemPrompt = {
    engineering: "What problem are you solving? Who has it?",
    prototype: "What do you want to learn?",
    vision: "Why does this matter, and where is it headed?",
    customer_docs: "Who is this for, and what do they need to understand?",
  }[specType];
  const criteriaPrompt = {
    engineering: "How will we know it works?",
    prototype: "What will you build to learn this?",
    vision: "How will you measure success?",
    customer_docs: "What sections should it cover?",
  }[specType];
  const criteriaMin = specType === "engineering" ? 3 : 1;

  // --- Task handlers ---

  const handleSaveTask = async () => {
    if (!taskTitle.trim()) return;
    setSaving(true);
    try {
      await api.post("/specs/draft", {
        title: taskTitle.trim(),
        kind: "task",
      });
      onComplete("");
    } catch {
      setSaving(false);
    }
  };

  // --- Spec wizard handlers ---

  const canAdvance = () => {
    if (step === "Type") return true;
    if (step === "Problem") return title.trim().length > 0 && problem.trim().length > 0;
    if (step === "Scope") return true;
    if (step === "Produces") return true;
    if (step === "Criteria") return criteria.length >= criteriaMin;
    return true;
  };

  const next = () => {
    const idx = SPEC_STEPS.indexOf(step);
    if (idx < SPEC_STEPS.length - 1) {
      const nextStep = SPEC_STEPS[idx + 1];
      if (nextStep === "Criteria" && criteria.length === 0) {
        handleSuggest();
      }
      setStep(nextStep);
    }
  };

  const back = () => {
    const idx = SPEC_STEPS.indexOf(step);
    if (idx > 0) setStep(SPEC_STEPS[idx - 1]);
  };

  const handleSuggest = async () => {
    setSuggesting(true);
    try {
      const data = await api.post<Suggestions>("/specs/wizard/suggest", {
        problem,
        in_scope: inScope.length > 0 ? inScope : undefined,
      });
      if (data.criteria.length > 0 && criteria.length === 0) {
        setCriteria(data.criteria);
      }
      if (data.non_goals.length > 0 && nonGoals.length === 0) {
        setNonGoals(data.non_goals);
      }
      if (data.after_statement && !afterStatement) {
        setAfterStatement(data.after_statement);
      }
    } catch {
      // AI suggestions are optional
    } finally {
      setSuggesting(false);
    }
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const data = await api.post<{ path: string }>("/specs/wizard/create", {
        title,
        problem: afterStatement ? `${problem}\n\n**After this ships:** ${afterStatement}` : problem,
        in_scope: inScope,
        out_of_scope: outOfScope,
        non_goals: nonGoals,
        criteria,
        kind: "spec",
        produces,
        type: specType,
      });
      onComplete(data.path || "");
    } catch {
      setCreating(false);
    }
  };

  const addToList = (
    list: string[],
    setList: (v: string[]) => void,
    value: string,
    setValue: (v: string) => void,
  ) => {
    const trimmed = value.trim();
    if (trimmed && !list.includes(trimmed)) {
      setList([...list, trimmed]);
      setValue("");
    }
  };

  const removeFromList = (list: string[], setList: (v: string[]) => void, index: number) => {
    setList(list.filter((_, i) => i !== index));
  };

  const ListEditor = ({
    items,
    setItems,
    newItem,
    setNewItem,
    placeholder,
    testId,
  }: {
    items: string[];
    setItems: (v: string[]) => void;
    newItem: string;
    setNewItem: (v: string) => void;
    placeholder: string;
    testId: string;
  }) => (
    <div data-testid={testId}>
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-2 mb-2 group">
          <span className="flex-1 text-sm text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800/50 rounded-lg px-3 py-2">
            {item}
          </span>
          <button
            onClick={() => removeFromList(items, setItems, i)}
            className="text-slate-500 hover:text-red-600 dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      ))}
      <div className="flex gap-2">
        <input
          type="text"
          value={newItem}
          onChange={(e) => setNewItem(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addToList(items, setItems, newItem, setNewItem);
          }}
          placeholder={placeholder}
          className="flex-1 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => addToList(items, setItems, newItem, setNewItem)}
          disabled={!newItem.trim()}
          className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 disabled:opacity-30"
        >
          <Icon name="add" size={20} />
        </button>
      </div>
    </div>
  );

  return (
    <div className="bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-xl p-6 mb-8" data-testid="spec-wizard">
      {/* Mode picker header */}
      <div className="flex items-center gap-1 mb-6">
        <div className="flex bg-slate-100 dark:bg-slate-800 rounded-lg p-1 gap-1 flex-1">
          <button
            onClick={() => setMode("task")}
            data-testid="mode-task"
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              mode === "task"
                ? "bg-slate-200 dark:bg-slate-700 text-white"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            <Icon name="add_task" size={16} />
            Task
          </button>
          <button
            onClick={() => setMode("spec")}
            data-testid="mode-spec"
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              mode === "spec"
                ? "bg-slate-200 dark:bg-slate-700 text-white"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            <Icon name="description" size={16} />
            Spec
          </button>
        </div>
        <button onClick={onCancel} className="ml-2 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
          <Icon name="close" size={20} />
        </button>
      </div>

      {/* Task mode */}
      {mode === "task" && (
        <div data-testid="task-mode">
          <p className="text-xs text-slate-500 mb-4">
            Use this for a bug fix, small feature, or anything a single agent can handle.
            Switch to Spec for multi-step features that need a problem statement and success criteria.
          </p>
          <div className="space-y-3">
            <div>
              <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">What needs doing?</label>
              <input
                type="text"
                value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && taskTitle.trim()) handleSaveTask(); }}
                placeholder="e.g. Fix dark mode flicker on settings page"
                className="w-full bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                data-testid="task-title"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">
                More detail <span className="text-slate-600">(optional)</span>
              </label>
              <input
                type="text"
                value={taskDesc}
                onChange={(e) => setTaskDesc(e.target.value)}
                placeholder="Any extra context..."
                className="w-full bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                data-testid="task-desc"
              />
            </div>
          </div>
          <div className="flex justify-end mt-5">
            <Button
              variant="primary"
              size="md"
              onClick={handleSaveTask}
              disabled={!taskTitle.trim() || saving}
              data-testid="task-submit"
            >
              {saving ? "Saving..." : "Add to my list"}
            </Button>
          </div>
        </div>
      )}

      {/* Spec mode */}
      {mode === "spec" && (
        <>
          <p className="text-xs text-slate-500 mb-4">
            Use this when the feature touches 3 or more files, changes user-facing behavior, or needs a
            problem statement and at least 3 success criteria to keep scope honest.
          </p>

          {/* Step indicators */}
          <div className="flex items-center gap-2 mb-6">
            {SPEC_STEPS.map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors ${
                    i < stepIndex
                      ? "bg-green-500/20 text-green-600 dark:text-green-400"
                      : i === stepIndex
                        ? "bg-blue-500/20 text-blue-600 dark:text-blue-400 ring-1 ring-blue-500"
                        : "bg-slate-100 dark:bg-slate-800 text-slate-500"
                  }`}
                >
                  {i < stepIndex ? <Icon name="check" size={14} /> : i + 1}
                </div>
                <span
                  className={`text-xs ${
                    i === stepIndex ? "text-white font-medium" : "text-slate-500"
                  }`}
                >
                  {s}
                </span>
                {i < SPEC_STEPS.length - 1 && (
                  <div className="w-8 h-px bg-slate-200 dark:bg-slate-700 mx-1" />
                )}
              </div>
            ))}
          </div>

          {/* Step content */}
          {step === "Type" && (
            <div data-testid="wizard-type" className="space-y-3">
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                What are you making? This shapes the template and what counts as "ready".
              </p>
              <div className="grid grid-cols-2 gap-3">
                {SPEC_TYPE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    data-testid={`spec-type-${opt.value}`}
                    onClick={() => setSpecType(opt.value)}
                    className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-colors ${
                      specType === opt.value
                        ? "border-blue-500 bg-blue-500/10 text-white"
                        : "border-slate-200 dark:border-slate-700 bg-slate-50/40 dark:bg-slate-800/40 text-slate-700 dark:text-slate-300 hover:border-slate-500"
                    }`}
                  >
                    <Icon name={opt.icon} size={20} className={specType === opt.value ? "text-blue-600 dark:text-blue-400" : "text-slate-500"} />
                    <div>
                      <div className="font-medium text-sm">{opt.label}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{opt.description}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === "Problem" && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Title</label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Name this spec..."
                  className="w-full bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                  data-testid="wizard-title"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">
                  {problemPrompt}
                </label>
                <textarea
                  value={problem}
                  onChange={(e) => setProblem(e.target.value)}
                  placeholder="e.g. PMs forget which tasks relate to their upcoming meetings, so they walk into meetings unprepared..."
                  rows={4}
                  className="w-full bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
                  data-testid="wizard-problem"
                />
              </div>
              {afterStatement && (
                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg px-4 py-3">
                  <p className="text-xs text-blue-600 dark:text-blue-400 mb-1">After this ships:</p>
                  <p className="text-sm text-white">{afterStatement}</p>
                </div>
              )}
            </div>
          )}

          {step === "Scope" && (
            <div className="space-y-6">
              <div>
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-2">
                  What should this include?
                </label>
                <ListEditor
                  items={inScope}
                  setItems={setInScope}
                  newItem={newScopeItem}
                  setNewItem={setNewScopeItem}
                  placeholder="e.g. Calendar page integration"
                  testId="wizard-in-scope"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-2">
                  What should this NOT include?
                </label>
                <ListEditor
                  items={outOfScope}
                  setItems={setOutOfScope}
                  newItem={newOutItem}
                  setNewItem={setNewOutItem}
                  placeholder="e.g. Meeting transcript parsing"
                  testId="wizard-out-of-scope"
                />
              </div>
            </div>
          )}

          {step === "Produces" && (
            <div data-testid="wizard-produces" className="space-y-3">
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                What should this spec produce when you click "Build it"?
              </p>
              <div className="grid grid-cols-2 gap-3">
                {PRODUCES_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    data-testid={`produces-${opt.value}`}
                    onClick={() => setProduces(opt.value)}
                    className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-colors ${
                      produces === opt.value
                        ? "border-blue-500 bg-blue-500/10 text-white"
                        : "border-slate-200 dark:border-slate-700 bg-slate-50/40 dark:bg-slate-800/40 text-slate-700 dark:text-slate-300 hover:border-slate-500"
                    }`}
                  >
                    <Icon name={opt.icon} size={20} className={produces === opt.value ? "text-blue-600 dark:text-blue-400" : "text-slate-500"} />
                    <div>
                      <div className="font-medium text-sm">{opt.label}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{opt.description}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === "Criteria" && (
            <div className="space-y-6">
              {suggesting && (
                <div className="flex items-center gap-2 text-sm text-blue-600 dark:text-blue-400 mb-2">
                  <Icon name="autorenew" size={16} className="animate-spin" />
                  Generating suggestions...
                </div>
              )}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm text-slate-600 dark:text-slate-400">
                    {criteriaPrompt} <span className="text-slate-600">(need at least {criteriaMin})</span>
                  </label>
                  <button
                    onClick={handleSuggest}
                    disabled={suggesting || !problem.trim()}
                    className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 disabled:opacity-30"
                  >
                    Regenerate suggestions
                  </button>
                </div>
                <ListEditor
                  items={criteria}
                  setItems={setCriteria}
                  newItem={newCriterion}
                  setNewItem={setNewCriterion}
                  placeholder="e.g. Shows related tasks for upcoming meetings"
                  testId="wizard-criteria"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-2">
                  What this does NOT do
                </label>
                <ListEditor
                  items={nonGoals}
                  setItems={setNonGoals}
                  newItem={newNonGoal}
                  setNewItem={setNewNonGoal}
                  placeholder="e.g. Does not auto-create tasks from meeting notes"
                  testId="wizard-non-goals"
                />
              </div>
            </div>
          )}

          {step === "Review" && (
            <div className="space-y-4 text-sm">
              <h3 className="text-white font-semibold text-base">{title}</h3>

              <div>
                <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Type</h4>
                <p className="text-slate-700 dark:text-slate-300">{SPEC_TYPE_OPTIONS.find(o => o.value === specType)?.label ?? specType}</p>
              </div>

              <div>
                <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Produces</h4>
                <p className="text-slate-700 dark:text-slate-300">{PRODUCES_OPTIONS.find(o => o.value === produces)?.label ?? produces}</p>
              </div>

              <div>
                <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Problem</h4>
                <p className="text-slate-700 dark:text-slate-300">{problem}</p>
                {afterStatement && (
                  <p className="text-blue-600 dark:text-blue-400 mt-1 text-xs">After this ships: {afterStatement}</p>
                )}
              </div>

              {inScope.length > 0 && (
                <div>
                  <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">In scope</h4>
                  <ul className="text-slate-700 dark:text-slate-300 space-y-0.5">
                    {inScope.map((s, i) => <li key={i} className="flex items-start gap-2"><Icon name="check_circle" size={14} className="text-green-600 dark:text-green-400 mt-0.5 shrink-0" />{s}</li>)}
                  </ul>
                </div>
              )}

              {outOfScope.length > 0 && (
                <div>
                  <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Out of scope</h4>
                  <ul className="text-slate-700 dark:text-slate-300 space-y-0.5">
                    {outOfScope.map((s, i) => <li key={i} className="flex items-start gap-2"><Icon name="block" size={14} className="text-red-600 dark:text-red-400 mt-0.5 shrink-0" />{s}</li>)}
                  </ul>
                </div>
              )}

              {criteria.length > 0 && (
                <div>
                  <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Success criteria</h4>
                  <ul className="text-slate-700 dark:text-slate-300 space-y-0.5">
                    {criteria.map((c, i) => <li key={i} className="flex items-start gap-2"><Icon name="radio_button_unchecked" size={14} className="text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />{c}</li>)}
                  </ul>
                </div>
              )}

              {nonGoals.length > 0 && (
                <div>
                  <h4 className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider mb-1">Non-goals</h4>
                  <ul className="text-slate-700 dark:text-slate-300 space-y-0.5">
                    {nonGoals.map((g, i) => <li key={i} className="flex items-start gap-2"><Icon name="do_not_disturb" size={14} className="text-slate-500 mt-0.5 shrink-0" />{g}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Spec navigation */}
          <div className="flex items-center justify-between mt-6 pt-4 border-t border-slate-200 dark:border-slate-800">
            <Button
              variant="secondary"
              size="sm"
              onClick={back}
              disabled={stepIndex === 0}
            >
              Back
            </Button>
            <div className="flex gap-2">
              {step === "Review" ? (
                <Button
                  variant="primary"
                  size="md"
                  onClick={handleCreate}
                  disabled={creating}
                  data-testid="spec-submit"
                >
                  {creating ? "Creating..." : "Create Spec"}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={next}
                  disabled={!canAdvance()}
                >
                  Next
                </Button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
