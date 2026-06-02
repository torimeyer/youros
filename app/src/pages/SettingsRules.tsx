import { useCallback, useEffect, useMemo, useState } from "react";
import PageShell from "../components/PageShell";
import { PageHeader } from "../components/ui";
import ConfirmModal from "../components/ConfirmModal";
import RuleActivityModal from "../components/RuleActivityModal";
import { api } from "../lib/api";

// ---- Types from /api/rules ----

type ParamType = "list[str]" | "int" | "bool" | "str" | string;

interface RuleParamMeta {
  type: ParamType;
  label: string;
  help?: string;
}

interface RuleMeta {
  title: string;
  description: string;
  category: string;
  params: Record<string, RuleParamMeta>;
}

export interface Rule {
  key: string;
  enabled: boolean;
  params: Record<string, unknown>;
  meta: RuleMeta;
  default_enabled: boolean;
  source: "default" | "user" | string;
}

// ---- Category display ----

// Order shown in the left nav. Categories not in this list still appear,
// at the end, in the order they arrive from the backend.
const CATEGORY_ORDER: { key: string; label: string }[] = [
  { key: "communication", label: "Communication" },
  { key: "agent_flow", label: "Agent flow" },
  { key: "tool_guards", label: "Tool guards" },
  { key: "prompt_header", label: "Prompt header" },
  { key: "quality", label: "Quality" },
  { key: "infrastructure", label: "Infrastructure" },
];

function categoryLabel(key: string): string {
  const known = CATEGORY_ORDER.find((c) => c.key === key);
  if (known) return known.label;
  // Fall back to a title-cased version of the raw key.
  return key
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

// ---- Small toast ----

interface ToastState {
  id: number;
  kind: "success" | "error";
  message: string;
}

function Toast({ toast }: { toast: ToastState | null }) {
  if (!toast) return null;
  const cls =
    toast.kind === "success"
      ? "bg-green-900/90 border-green-700 text-green-100"
      : "bg-red-900/90 border-red-700 text-red-100";
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid={`toast-${toast.kind}`}
      className={`fixed bottom-6 right-6 z-50 border rounded-lg px-4 py-3 text-sm shadow-2xl ${cls}`}
    >
      {toast.message}
    </div>
  );
}

// ---- Toggle ----

function Toggle({
  checked,
  onChange,
  testId,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  testId?: string;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      onClick={onChange}
      className={`relative w-11 h-6 rounded-full transition-colors flex-shrink-0 ${
        checked ? "bg-blue-500" : "bg-slate-200 dark:bg-slate-700"
      } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform ${
          checked ? "translate-x-5" : ""
        }`}
      />
    </button>
  );
}

// ---- Param editors ----

function paramToInputString(value: unknown, type: ParamType): string {
  if (type === "list[str]") {
    if (Array.isArray(value)) return value.join(", ");
    return "";
  }
  if (type === "int") {
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return value;
    return "";
  }
  if (typeof value === "string") return value;
  if (value == null) return "";
  return String(value);
}

function parseParamInput(raw: string, type: ParamType): unknown {
  if (type === "list[str]") {
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }
  if (type === "int") {
    const n = parseInt(raw, 10);
    return Number.isNaN(n) ? 0 : n;
  }
  // str
  return raw;
}

interface ParamFieldProps {
  ruleKey: string;
  paramKey: string;
  meta: RuleParamMeta;
  value: unknown;
  onCommit: (paramKey: string, newValue: unknown) => void;
}

function ParamField({ ruleKey, paramKey, meta, value, onCommit }: ParamFieldProps) {
  const testIdBase = `rule-${ruleKey}-param-${paramKey}`;
  if (meta.type === "bool") {
    const checked = Boolean(value);
    return (
      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
        <input
          type="checkbox"
          data-testid={testIdBase}
          checked={checked}
          onChange={() => onCommit(paramKey, !checked)}
          className="w-4 h-4 rounded border-slate-600 bg-slate-100 dark:bg-slate-800"
        />
        <span>{meta.label}</span>
        {meta.help && (
          <span className="text-slate-500 text-xs ml-2">{meta.help}</span>
        )}
      </label>
    );
  }

  // Text/number/list use a local draft so the user can type freely. We
  // commit on blur, not on each keystroke, to avoid spamming the
  // backend with one PUT per character.
  return (
    <ParamTextLikeField
      ruleKey={ruleKey}
      paramKey={paramKey}
      meta={meta}
      value={value}
      onCommit={onCommit}
    />
  );
}

function ParamTextLikeField({
  ruleKey,
  paramKey,
  meta,
  value,
  onCommit,
}: ParamFieldProps) {
  const initial = paramToInputString(value, meta.type);
  const [draft, setDraft] = useState(initial);
  // If the upstream value changes (e.g. reset to default), pull the new
  // value into the draft so the field reflects reality.
  useEffect(() => {
    setDraft(paramToInputString(value, meta.type));
    // We intentionally only re-sync when the upstream value changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const testIdBase = `rule-${ruleKey}-param-${paramKey}`;
  const commit = () => {
    const parsed = parseParamInput(draft, meta.type);
    // Only fire a write when the parsed value actually differs from the
    // upstream value. Avoids a PUT just because the user clicked into
    // and out of the field without changing anything.
    const same =
      meta.type === "list[str]"
        ? Array.isArray(value) &&
          Array.isArray(parsed) &&
          (value as unknown[]).length === (parsed as unknown[]).length &&
          (value as unknown[]).every(
            (v, i) => v === (parsed as unknown[])[i],
          )
        : parsed === value;
    if (!same) onCommit(paramKey, parsed);
  };

  const inputType = meta.type === "int" ? "number" : "text";

  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-slate-700 dark:text-slate-300">{meta.label}</span>
      {meta.help && <span className="text-slate-500 text-xs">{meta.help}</span>}
      <input
        type={inputType}
        data-testid={testIdBase}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        className="bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-white text-sm rounded px-2 py-1.5 focus:outline-none focus:border-blue-500"
      />
    </label>
  );
}

// ---- Rule card ----

interface RuleCardProps {
  rule: Rule;
  onToggle: (rule: Rule, next: boolean) => void;
  onParamChange: (rule: Rule, paramKey: string, value: unknown) => void;
  onReset: (rule: Rule) => void;
  onOpenActivity: (rule: Rule) => void;
}

function RuleCard({
  rule,
  onToggle,
  onParamChange,
  onReset,
  onOpenActivity,
}: RuleCardProps) {
  const paramEntries = Object.entries(rule.meta.params ?? {});
  return (
    <div
      data-testid={`rule-card-${rule.key}`}
      className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5 mb-4"
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-white font-semibold text-base">
              {rule.meta.title}
            </h3>
            <span
              className={`text-xs px-2 py-0.5 rounded-full ${
                rule.source === "user"
                  ? "bg-blue-900/60 text-blue-700 dark:text-blue-300 border border-blue-800"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700"
              }`}
              data-testid={`rule-source-${rule.key}`}
            >
              {rule.source === "user" ? "user override" : "default"}
            </span>
          </div>
          <p className="text-slate-600 dark:text-slate-400 text-sm mt-1">{rule.meta.description}</p>
        </div>
        <Toggle
          checked={rule.enabled}
          onChange={() => onToggle(rule, !rule.enabled)}
          testId={`rule-toggle-${rule.key}`}
          label={`Enable ${rule.meta.title}`}
        />
      </div>

      {paramEntries.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          {paramEntries.map(([paramKey, paramMeta]) => (
            <ParamField
              key={paramKey}
              ruleKey={rule.key}
              paramKey={paramKey}
              meta={paramMeta}
              value={rule.params[paramKey]}
              onCommit={(pk, v) => onParamChange(rule, pk, v)}
            />
          ))}
        </div>
      )}

      <div className="flex items-center gap-3 mt-4 text-sm">
        <button
          type="button"
          data-testid={`rule-reset-${rule.key}`}
          onClick={() => onReset(rule)}
          className="text-slate-600 dark:text-slate-400 hover:text-white transition-colors"
        >
          Reset to default
        </button>
        <button
          type="button"
          data-testid={`rule-activity-link-${rule.key}`}
          onClick={() => onOpenActivity(rule)}
          className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
        >
          Recent activity
        </button>
      </div>
    </div>
  );
}

// ---- Page ----

export default function SettingsRules() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [resetAllOpen, setResetAllOpen] = useState(false);
  const [activityRule, setActivityRule] = useState<Rule | null>(null);

  const showToast = useCallback((kind: "success" | "error", message: string) => {
    const t: ToastState = { id: Date.now(), kind, message };
    setToast(t);
    setTimeout(() => {
      setToast((current) => (current && current.id === t.id ? null : current));
    }, 3000);
  }, []);

  const loadRules = useCallback(async () => {
    setLoadError(null);
    try {
      const rows = await api.get<Rule[]>("/rules");
      setRules(Array.isArray(rows) ? rows : []);
    } catch {
      setLoadError("Couldn't load rules. Try again.");
    }
  }, []);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  // Group rules by category, ordered as configured above.
  const grouped = useMemo(() => {
    const buckets = new Map<string, Rule[]>();
    for (const r of rules ?? []) {
      const cat = r.meta?.category || "uncategorized";
      const arr = buckets.get(cat) ?? [];
      arr.push(r);
      buckets.set(cat, arr);
    }
    const orderedKeys = [
      ...CATEGORY_ORDER.map((c) => c.key).filter((k) => buckets.has(k)),
      ...Array.from(buckets.keys()).filter(
        (k) => !CATEGORY_ORDER.some((c) => c.key === k),
      ),
    ];
    return orderedKeys.map((k) => ({
      key: k,
      label: categoryLabel(k),
      rules: buckets.get(k) ?? [],
    }));
  }, [rules]);

  // Default the active category to the first one that has rules.
  useEffect(() => {
    if (activeCategory == null && grouped.length > 0) {
      setActiveCategory(grouped[0].key);
    }
  }, [grouped, activeCategory]);

  // Apply an optimistic patch to a single rule and call the backend. If
  // the backend fails, roll back to the previous state and toast the
  // error.
  const patchRule = useCallback(
    async (rule: Rule, body: { enabled?: boolean; params?: Record<string, unknown> }) => {
      const prevSnapshot = rules;
      setRules((current) =>
        (current ?? []).map((r) =>
          r.key !== rule.key
            ? r
            : {
                ...r,
                enabled: body.enabled !== undefined ? body.enabled : r.enabled,
                params: body.params
                  ? { ...r.params, ...body.params }
                  : r.params,
                source: "user",
              },
        ),
      );
      try {
        const updated = await api.put<Rule>(
          `/rules/${encodeURIComponent(rule.key)}`,
          body,
        );
        // Trust the server's view of the row over our optimistic patch.
        setRules((current) =>
          (current ?? []).map((r) => (r.key === updated.key ? updated : r)),
        );
        showToast("success", "Saved. Active on next tool call.");
      } catch {
        setRules(prevSnapshot ?? null);
        showToast("error", "Couldn't save. Try again.");
      }
    },
    [rules, showToast],
  );

  const handleToggle = useCallback(
    (rule: Rule, next: boolean) => {
      patchRule(rule, { enabled: next });
    },
    [patchRule],
  );

  const handleParamChange = useCallback(
    (rule: Rule, paramKey: string, value: unknown) => {
      patchRule(rule, { params: { [paramKey]: value } });
    },
    [patchRule],
  );

  const handleResetRule = useCallback(
    async (rule: Rule) => {
      try {
        await api.post(`/rules/reset`, { key: rule.key });
        showToast("success", "Saved. Active on next tool call.");
        await loadRules();
      } catch {
        showToast("error", "Couldn't save. Try again.");
      }
    },
    [loadRules, showToast],
  );

  const handleResetAll = useCallback(async () => {
    setResetAllOpen(false);
    try {
      await api.post(`/rules/reset`, {});
      showToast("success", "Saved. Active on next tool call.");
      await loadRules();
    } catch {
      showToast("error", "Couldn't save. Try again.");
    }
  }, [loadRules, showToast]);

  const activeGroup = grouped.find((g) => g.key === activeCategory) ?? null;

  return (
    <PageShell title="Rules" fullHeight>
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        <PageHeader
          title="Rules"
          subtitle="Turn each rule on or off, tweak its settings, or see when it last fired."
          primaryAction={
            <button
              type="button"
              data-testid="reset-all-rules"
              onClick={() => setResetAllOpen(true)}
              className="px-3 py-1.5 text-sm border border-slate-200 dark:border-slate-700 hover:border-slate-500 text-slate-700 dark:text-slate-300 hover:text-white rounded-lg transition-colors"
            >
              Reset all rules
            </button>
          }
        />

        {loadError && (
          <div
            className="bg-red-900/40 border border-red-800 text-red-200 text-sm rounded-lg px-4 py-3 mb-4"
            data-testid="rules-load-error"
          >
            {loadError}
          </div>
        )}

        {rules && rules.length === 0 && !loadError && (
          <div className="text-slate-500 text-sm" data-testid="rules-empty">
            No rules available.
          </div>
        )}

        {rules && rules.length > 0 && (
          <div className="flex gap-6">
            <nav
              className="w-56 shrink-0"
              aria-label="Rule categories"
              data-testid="rule-categories-nav"
            >
              <ul className="space-y-1">
                {grouped.map((g) => {
                  const active = g.key === activeCategory;
                  return (
                    <li key={g.key}>
                      <button
                        type="button"
                        data-testid={`rule-category-${g.key}`}
                        onClick={() => setActiveCategory(g.key)}
                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                          active
                            ? "bg-slate-100 dark:bg-slate-800 text-white"
                            : "text-slate-600 dark:text-slate-400 hover:text-white hover:bg-white dark:hover:bg-slate-900"
                        }`}
                        aria-current={active ? "page" : undefined}
                      >
                        <span>{g.label}</span>
                        <span className="ml-2 text-xs text-slate-500">
                          {g.rules.length}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </nav>

            <div className="flex-1 min-w-0">
              {activeGroup &&
                activeGroup.rules.map((r) => (
                  <RuleCard
                    key={r.key}
                    rule={r}
                    onToggle={handleToggle}
                    onParamChange={handleParamChange}
                    onReset={handleResetRule}
                    onOpenActivity={(rule) => setActivityRule(rule)}
                  />
                ))}
            </div>
          </div>
        )}
      </div>

      <ConfirmModal
        open={resetAllOpen}
        title="Reset all rules?"
        message="This removes every change you made and puts each rule back to its default setting. You can change them again afterwards."
        confirmLabel="Reset all"
        cancelLabel="Cancel"
        danger
        onConfirm={handleResetAll}
        onCancel={() => setResetAllOpen(false)}
      />

      <RuleActivityModal
        open={activityRule != null}
        ruleKey={activityRule?.key ?? ""}
        ruleTitle={activityRule?.meta?.title ?? ""}
        onClose={() => setActivityRule(null)}
      />

      <Toast toast={toast} />
    </PageShell>
  );
}
