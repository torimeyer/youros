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

const STEPS = ["Problem", "Scope", "Criteria", "Review"] as const;
type Step = typeof STEPS[number];

export default function SpecWizard({ onComplete, onCancel }: Props) {
  const [step, setStep] = useState<Step>("Problem");
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
  const [suggesting, setSuggesting] = useState(false);
  const [creating, setCreating] = useState(false);

  const stepIndex = STEPS.indexOf(step);

  const canAdvance = () => {
    if (step === "Problem") return title.trim().length > 0 && problem.trim().length > 0;
    if (step === "Scope") return true;
    if (step === "Criteria") return criteria.length > 0;
    return true;
  };

  const next = () => {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) {
      const nextStep = STEPS[idx + 1];
      if (nextStep === "Criteria" && criteria.length === 0) {
        handleSuggest();
      }
      setStep(nextStep);
    }
  };

  const back = () => {
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
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
      });
      onComplete(data.path);
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
          <span className="flex-1 text-sm text-slate-300 bg-slate-800/50 rounded-lg px-3 py-2">
            {item}
          </span>
          <button
            onClick={() => removeFromList(items, setItems, i)}
            className="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
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
          className="flex-1 bg-slate-900/40 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => addToList(items, setItems, newItem, setNewItem)}
          disabled={!newItem.trim()}
          className="text-blue-400 hover:text-blue-300 disabled:opacity-30"
        >
          <Icon name="add" size={20} />
        </button>
      </div>
    </div>
  );

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 mb-8" data-testid="spec-wizard">
      {/* Step indicators */}
      <div className="flex items-center gap-2 mb-6">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors ${
                i < stepIndex
                  ? "bg-green-500/20 text-green-400"
                  : i === stepIndex
                    ? "bg-blue-500/20 text-blue-400 ring-1 ring-blue-500"
                    : "bg-slate-800 text-slate-500"
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
            {i < STEPS.length - 1 && (
              <div className="w-8 h-px bg-slate-700 mx-1" />
            )}
          </div>
        ))}
        <div className="flex-1" />
        <button onClick={onCancel} className="text-slate-500 hover:text-slate-300">
          <Icon name="close" size={20} />
        </button>
      </div>

      {/* Step content */}
      {step === "Problem" && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Name this spec..."
              className="w-full bg-slate-900/40 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
              data-testid="wizard-title"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1">
              What problem are you solving? Who has it?
            </label>
            <textarea
              value={problem}
              onChange={(e) => setProblem(e.target.value)}
              placeholder="e.g. PMs forget which tasks relate to their upcoming meetings, so they walk into meetings unprepared..."
              rows={4}
              className="w-full bg-slate-900/40 border border-slate-700 rounded-lg px-4 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
              data-testid="wizard-problem"
            />
          </div>
          {afterStatement && (
            <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg px-4 py-3">
              <p className="text-xs text-blue-400 mb-1">After this ships:</p>
              <p className="text-sm text-white">{afterStatement}</p>
            </div>
          )}
        </div>
      )}

      {step === "Scope" && (
        <div className="space-y-6">
          <div>
            <label className="block text-sm text-slate-400 mb-2">
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
            <label className="block text-sm text-slate-400 mb-2">
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

      {step === "Criteria" && (
        <div className="space-y-6">
          {suggesting && (
            <div className="flex items-center gap-2 text-sm text-blue-400 mb-2">
              <Icon name="autorenew" size={16} className="animate-spin" />
              Generating suggestions...
            </div>
          )}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm text-slate-400">
                How will we know it works?
              </label>
              <button
                onClick={handleSuggest}
                disabled={suggesting || !problem.trim()}
                className="text-xs text-blue-400 hover:text-blue-300 disabled:opacity-30"
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
            <label className="block text-sm text-slate-400 mb-2">
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
            <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-1">Problem</h4>
            <p className="text-slate-300">{problem}</p>
            {afterStatement && (
              <p className="text-blue-400 mt-1 text-xs">After this ships: {afterStatement}</p>
            )}
          </div>

          {inScope.length > 0 && (
            <div>
              <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-1">In scope</h4>
              <ul className="text-slate-300 space-y-0.5">
                {inScope.map((s, i) => <li key={i} className="flex items-start gap-2"><Icon name="check_circle" size={14} className="text-green-400 mt-0.5 shrink-0" />{s}</li>)}
              </ul>
            </div>
          )}

          {outOfScope.length > 0 && (
            <div>
              <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-1">Out of scope</h4>
              <ul className="text-slate-300 space-y-0.5">
                {outOfScope.map((s, i) => <li key={i} className="flex items-start gap-2"><Icon name="block" size={14} className="text-red-400 mt-0.5 shrink-0" />{s}</li>)}
              </ul>
            </div>
          )}

          {criteria.length > 0 && (
            <div>
              <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-1">Success criteria</h4>
              <ul className="text-slate-300 space-y-0.5">
                {criteria.map((c, i) => <li key={i} className="flex items-start gap-2"><Icon name="radio_button_unchecked" size={14} className="text-blue-400 mt-0.5 shrink-0" />{c}</li>)}
              </ul>
            </div>
          )}

          {nonGoals.length > 0 && (
            <div>
              <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-1">Non-goals</h4>
              <ul className="text-slate-300 space-y-0.5">
                {nonGoals.map((g, i) => <li key={i} className="flex items-start gap-2"><Icon name="do_not_disturb" size={14} className="text-slate-500 mt-0.5 shrink-0" />{g}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Navigation */}
      <div className="flex items-center justify-between mt-6 pt-4 border-t border-slate-800">
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
    </div>
  );
}
