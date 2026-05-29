import { useState, useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import SpecTemplateDetailsModal, {
  type SpecTemplateDetailsValues,
} from "../components/SpecTemplateDetailsModal";
import { api } from "../lib/api";
import { formatRelative } from "../lib/time";
import { reportError } from '../lib/reportError';
import { buildSpec } from "../lib/spawn";
import { NeedsClarityChip, type ReadinessCheck } from "../components/NeedsClarityChip";
import { ClaimSourceChip } from "../components/ClaimSourceChip";
import { SpawnGeminiModal } from "../components/SpawnGeminiModal";
import { onSpecsChange, bumpAgents, bumpTasks } from "../lib/sidebarBus";
import { useAppStore } from "../stores/app";
import { Button, EmptyState, ErrorBanner } from "../components/ui";
import { SpecDriveSync } from "../components/SpecDriveSync";
import SpecWizard from "../components/SpecWizard";

// --- Data types ---

interface TaskSummary {
  total: number;
  open: number;
  closed: number;
}

interface AcceptanceCriterion {
  text: string;
  checked: boolean;
}

interface LinkedTask {
  id: string;
  title: string;
  status: "open" | "closed";
  assigned_agent?: string | null;
}

interface Spec {
  path: string;
  filename: string;
  title: string;
  spec_id?: string;
  status: "draft" | "ready" | "in-progress" | "complete";
  created_at: string;
  promoted_at: string;
  body: string;
  task_summary?: TaskSummary;
  acceptance_criteria?: AcceptanceCriterion[];
  task_ids?: string[];
  clear_to_build?: boolean;
  clear_to_build_checks?: ReadinessCheck[];
  needs_clarity?: boolean;
  effective_status?: string;
  stage?: "draft" | "ready" | "in_progress";
  husk?: boolean;
  husk_reason?: string;
  missing_files?: string[];
  open_linked_needles?: string[];
}

interface SpecsResponse {
  docs: Spec[];
}

interface SpecTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  goal_markdown?: string;
  acceptance_criteria?: string[];
  tasks?: string[];
  produces_doc?: boolean;
}

interface SpecTemplatesResponse {
  templates: SpecTemplate[];
}

interface SpecClaim {
  agent: string;
  source: string;
  started_at: string;
  task_ids: string[];
}

interface SpecTasksResponse {
  tasks: LinkedTask[];
  claims?: SpecClaim[];
}


// Note: the response shape from /specs/{path}/build is now typed inside
// lib/spawn (BuildResult). Specs.tsx receives the BuildResult via
// buildSpec() rather than typing the raw response itself.

// Map legacy "spec" status to "ready" for display
function normalizeStatus(status: string): Spec["status"] {
  if (status === "spec") return "ready";
  // "building" is written to the spec frontmatter by build_spec() and by
  // spawn_agent when spec_id is set. compute_spec_status maps it to
  // "in-progress" when tasks exist, but normalizeStatus must also handle
  // it directly in case a future path returns "building" from the API. (→1420)
  if (status === "building") return "in-progress";
  if (status === "draft" || status === "ready" || status === "in-progress" || status === "complete") {
    return status;
  }
  return "draft";
}

// Single source of truth for backend-status to user-facing label.
// Backend uses: draft, spec (legacy), ready, in-progress, building, complete.
// User-facing labels: Draft, Ready, Building, Done.
// Any code that needs to show status text MUST call this function
// instead of repeating the mapping inline. Legacy "spec" is treated
// as "ready" here too so callers never need to pre-normalize.
export function displayStatus(backendStatus: string): "Draft" | "Ready" | "Building" | "Done" {
  if (backendStatus === "complete") return "Done";
  if (backendStatus === "in-progress") return "Building";
  if (backendStatus === "ready" || backendStatus === "spec") return "Ready";
  return "Draft";
}

type StageFilter = "all" | "draft" | "ready" | "in_progress" | "complete";

function getDocStage(doc: Spec): string {
  if (doc.stage) return doc.stage;
  const s = doc.status;
  if (s === "complete") return "complete";
  if (s === "in-progress") return "in_progress";
  if (s === "ready") return "ready";
  return "draft";
}

const STAGE_CHIP_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  draft:       { bg: "bg-slate-500/20",  text: "text-slate-600 dark:text-slate-400",  label: "Draft" },
  ready:       { bg: "bg-blue-500/20",   text: "text-blue-600 dark:text-blue-400",   label: "Ready" },
  in_progress: { bg: "bg-yellow-500/20", text: "text-yellow-600 dark:text-yellow-400", label: "In Progress" },
  complete:    { bg: "bg-green-500/20",  text: "text-green-600 dark:text-green-400",  label: "Done" },
};

function StageChip({ stage, title }: { stage: string; title?: string }) {
  const style = STAGE_CHIP_STYLES[stage] ?? STAGE_CHIP_STYLES.draft;
  return (
    <span
      className={`inline-flex items-center justify-center text-center px-2 py-1 min-h-[20px] rounded-full text-xs font-medium ${style.bg} ${style.text}`}
      data-testid="stage-chip"
      title={title}
    >
      {style.label}
    </span>
  );
}


// --- Task progress bar component ---

function TaskProgressBar({ summary }: { summary: TaskSummary }) {
  const { total, closed } = summary;
  if (total === 0) return null;
  const pct = Math.round((closed / total) * 100);
  return (
    <div className="flex items-center gap-2" data-testid="task-progress">
      <div className="flex-1 h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden max-w-[120px]">
        <div
          className="h-full bg-blue-500 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-slate-600 dark:text-slate-400 whitespace-nowrap">
        {closed}/{total} tasks
      </span>
    </div>
  );
}

// --- Simple markdown body renderer ---

export function SpecBody({ body }: { body: string }) {
  // Render markdown body as simple formatted text.
  // Splits on headings and renders paragraphs, bold, inline code, lists, fenced code blocks, and GFM pipe tables.
  const lines = body.split("\n");
  const elements: React.ReactNode[] = [];
  let key = 0;
  let inCodeBlock = false;
  const codeLines: string[] = [];
  const tableLines: string[] = [];

  const flushCodeBlock = () => {
    elements.push(
      <pre
        key={key++}
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded p-3 my-2 overflow-x-auto font-mono text-xs text-slate-800 dark:text-slate-200 whitespace-pre"
      >
        <code>{codeLines.join("\n")}</code>
      </pre>
    );
    codeLines.length = 0;
  };

  const isSeparatorRow = (row: string) =>
    /^\|[\s:]*[-:]+[\s:]*(\|[\s:]*[-:]+[\s:]*)*\|?\s*$/.test(row);

  const parseTableCells = (row: string): string[] => {
    const inner = row.replace(/^\|/, "").replace(/\|$/, "");
    return inner.split("|").map((c) => c.trim());
  };

  const flushTable = () => {
    if (tableLines.length === 0) return;
    const sepIdx = tableLines.findIndex(isSeparatorRow);
    const headerLines = sepIdx > 0 ? tableLines.slice(0, sepIdx) : [];
    const bodyLines = sepIdx >= 0 ? tableLines.slice(sepIdx + 1) : tableLines;
    elements.push(
      <div key={key++} className="overflow-x-auto my-2">
        <table className="text-xs text-slate-300 border-collapse w-full">
          {headerLines.length > 0 && (
            <thead>
              {headerLines.map((row, ri) => (
                <tr key={ri} className="border-b border-slate-700">
                  {parseTableCells(row).map((cell, ci) => (
                    <th key={ci} className="px-2 py-1 text-left font-semibold text-slate-200">
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
          )}
          <tbody>
            {bodyLines.map((row, ri) => (
              <tr key={ri} className="border-b border-slate-800">
                {parseTableCells(row).map((cell, ci) => (
                  <td key={ci} className="px-2 py-1">
                    {renderInline(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
    tableLines.length = 0;
  };

  for (const line of lines) {
    // Fenced code block boundary (``` with optional language tag)
    if (line.trimStart().startsWith("```")) {
      flushTable();
      if (!inCodeBlock) {
        inCodeBlock = true;
      } else {
        inCodeBlock = false;
        flushCodeBlock();
      }
      continue;
    }

    if (inCodeBlock) {
      // Preserve exact line content — no trimming — so ASCII art stays aligned
      codeLines.push(line);
      continue;
    }

    const trimmed = line.trimStart();

    // GFM pipe table rows
    if (trimmed.startsWith("|")) {
      tableLines.push(trimmed);
      continue;
    }

    flushTable();

    if (trimmed.startsWith("### ")) {
      elements.push(
        <h4 key={key++} className="text-xs font-semibold text-slate-800 dark:text-slate-200 mt-2 mb-0.5">
          {trimmed.slice(4)}
        </h4>
      );
    } else if (trimmed.startsWith("## ")) {
      elements.push(
        <h3 key={key++} className="text-sm font-semibold text-white mt-3 mb-1">
          {trimmed.slice(3)}
        </h3>
      );
    } else if (trimmed.startsWith("# ")) {
      elements.push(
        <h2 key={key++} className="text-base font-bold text-white mt-3 mb-1">
          {trimmed.slice(2)}
        </h2>
      );
    } else if (trimmed.startsWith("- [ ] ") || trimmed.startsWith("- [x] ")) {
      // skip acceptance criteria lines here, they are rendered in a dedicated section
      continue;
    } else if (trimmed.startsWith("- ")) {
      elements.push(
        <li key={key++} className="text-sm text-slate-700 dark:text-slate-300 ml-4 list-disc">
          {renderInline(trimmed.slice(2))}
        </li>
      );
    } else if (trimmed === "") {
      elements.push(<div key={key++} className="h-2" />);
    } else {
      elements.push(
        <p key={key++} className="text-sm text-slate-700 dark:text-slate-300">
          {renderInline(trimmed)}
        </p>
      );
    }
  }

  // Flush any trailing table or unclosed code block
  flushTable();
  if (inCodeBlock && codeLines.length > 0) {
    flushCodeBlock();
  }

  return <div className="space-y-0.5">{elements}</div>;
}

/** Render inline markdown: **bold**, `code` */
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-bold text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded text-[11px] font-mono text-amber-700 dark:text-amber-300">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part || null;
  });
}

// --- Acceptance criteria list (read-only status pills) ---
//
// Acceptance criteria render as colored status pills, not clickable
// checkboxes. A PM looking at a checkbox will try to click it, and
// flipping an AC by hand would drift from the real test outcome.
// Each pill shows a green filled circle when the criterion has been
// verified, or a hollow circle when it has not. A tooltip explains
// that the pill flips to green when Verify passes.

function AcceptanceCriteria({ criteria }: { criteria: AcceptanceCriterion[] }) {
  if (criteria.length === 0) return null;
  return (
    <div className="mt-3" data-testid="acceptance-criteria">
      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
        Acceptance Criteria
      </h4>
      <ul role="list" className="space-y-1.5">
        {criteria.map((c, i) => (
          <li key={i} className="flex items-start gap-2 text-sm">
            <span
              role="img"
              aria-label={c.checked ? "Done" : "Not done"}
              title="Acceptance criteria flip to green when Verify passes"
              data-testid="ac-status-pill"
              data-state={c.checked ? "done" : "not-done"}
              className={`mt-0.5 w-4 h-4 rounded-full flex-shrink-0 flex items-center justify-center ${
                c.checked
                  ? "bg-green-500 border border-green-500"
                  : "bg-transparent border border-slate-500"
              }`}
            />
            <span className={c.checked ? "text-slate-500 line-through" : "text-slate-700 dark:text-slate-300"}>
              {c.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Linked tasks list ---

function LinkedTasksList({
  tasks,
  building,
  onOpenAgent,
}: {
  tasks: LinkedTask[];
  building: boolean;
  onOpenAgent: (name: string) => void;
}) {
  if (tasks.length === 0) return null;
  const closedCount = tasks.filter((t) => t.status === "closed").length;
  return (
    <div className="mt-3" data-testid="linked-tasks">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Linked Tasks
        </h4>
        {building && (
          <span
            className="text-xs text-blue-700 dark:text-blue-300 font-medium"
            data-testid="build-progress-count"
          >
            {closedCount} of {tasks.length} tasks built
          </span>
        )}
      </div>
      <ul className="space-y-1.5">
        {tasks.map((t) => {
          const isDone = t.status === "closed";
          const agent = t.assigned_agent || undefined;
          return (
            <li
              key={t.id}
              className="flex items-center gap-2 text-sm"
              data-testid="linked-task-row"
              data-task-status={t.status}
            >
              {isDone ? (
                <span
                  className="w-4 h-4 rounded-full bg-green-500/20 border border-green-500 flex items-center justify-center flex-shrink-0"
                  data-testid="task-done-check"
                >
                  <Icon name="check" className="text-green-600 dark:text-green-400" size={12} />
                </span>
              ) : building && agent ? (
                <span
                  className="w-4 h-4 flex items-center justify-center flex-shrink-0"
                  data-testid="task-building-spinner"
                >
                  <span className="block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                </span>
              ) : (
                <span
                  className={`w-2 h-2 rounded-full ml-1 flex-shrink-0 ${
                    isDone ? "bg-green-500" : "bg-blue-500"
                  }`}
                />
              )}
              <span className="text-slate-600 dark:text-slate-400 font-mono text-xs">{t.id}</span>
              <span
                className={
                  isDone ? "text-slate-500 line-through" : "text-slate-700 dark:text-slate-300"
                }
              >
                {t.title}
              </span>
              {building && !isDone && agent && (
                <span
                  className="text-xs text-blue-700 dark:text-blue-300"
                  data-testid="task-building-label"
                >
                  Building...
                </span>
              )}
              {agent && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenAgent(agent);
                  }}
                  className="ml-auto text-[11px] font-mono text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:underline"
                  data-testid="task-agent-link"
                  title={`Open ${agent} in Agents tab`}
                >
                  {isDone ? `by: ${agent}` : agent}
                </button>
              )}
              {isDone && !agent && (
                <span className="text-xs text-green-500 ml-auto">Done</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- Main page ---

const SPECS_ONBOARDING_KEY = "specs-onboarding-dismissed";

function SpecsOnboarding() {
  const [dismissed, setDismissed] = useState(() =>
    localStorage.getItem(SPECS_ONBOARDING_KEY) === "1"
  );

  if (dismissed) return null;

  const handleDismiss = () => {
    setDismissed(true);
    localStorage.setItem(SPECS_ONBOARDING_KEY, "1");
  };

  return (
    <div className="bg-slate-100 border border-slate-200 dark:bg-slate-800/40 dark:border-slate-700/50 rounded-xl px-5 py-4 mb-6 relative">
      <button
        onClick={handleDismiss}
        className="absolute top-3 right-3 text-slate-500 hover:text-slate-700 dark:hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
        aria-label="Dismiss"
      >
        <Icon name="close" size={16} />
      </button>
      <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-1.5">What are Specs?</h3>
      <p className="text-sm text-slate-700 dark:text-slate-400 leading-relaxed">
        Specs let you define what you want built, break it into tasks, and have AI agents build it.
      </p>
      <p className="text-sm text-slate-700 dark:text-slate-400 leading-relaxed mt-1">
        The flow: Draft a spec with acceptance criteria &gt; Promote to lock it in &gt; Break into tasks &gt; Build with agents &gt; Verify the work.
      </p>
    </div>
  );
}

const SPECS_CACHE_KEY = 'myos.specsCache.v1'

function readSpecsCache(): Spec[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(SPECS_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as Spec[]) : []
  } catch { return [] }
}

function writeSpecsCache(docs: Spec[]): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(SPECS_CACHE_KEY, JSON.stringify(docs))
  } catch { /* quota errors are not fatal */ }
}

export default function Specs({ embedded }: { embedded?: boolean } = {}) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const focusParam = searchParams.get("focus");
  const specRowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const hasScrolledForFocusRef = useRef<string | null>(null);
  const [stageFilter, setStageFilter] = useState<StageFilter>("all");
  const [spawnGeminiSpec, setSpawnGeminiSpec] = useState<{ path: string; title: string; checks?: ReadinessCheck[] } | null>(null);
  const [docs, setDocs] = useState<Spec[]>(() => readSpecsCache());
  // Pending specs that were optimistically navigated here from
  // FilePreviewPane's "Make spec" click. The skeleton rows render in the
  // "all" and "drafts" tabs so the user sees motion the instant they
  // arrive, without waiting for backend AC generation (3-12 seconds on
  // Haiku). The cleanup effect below removes a pending row once the real
  // spec appears in docs.
  const pendingSpecsMap = useAppStore((s) => s.pendingSpecs);
  const removePendingSpec = useAppStore((s) => s.removePendingSpec);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"success" | "error">("success");
  const [decomposeToast, setDecomposeToast] = useState<{ count: number } | null>(null);
  // decomposeError UI stays for future use when build-time decompose failures
  // surface a dedicated banner. Wave 2 removed the setter since no user
  // action calls decompose directly anymore.
  const [decomposeError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [linkedTasks, setLinkedTasks] = useState<Record<string, LinkedTask[]>>({});
  const [claimsMap, setClaimsMap] = useState<Record<string, SpecClaim[]>>({});
  const [buildingSpec, setBuildingSpec] = useState<string | null>(null);
  const [buildResult, setBuildResult] = useState<Record<string, { agents: string[]; message: string; has_unchecked_acs?: boolean }>>({});
  const [templates, setTemplates] = useState<SpecTemplate[]>([]);
  const [templateLoading, setTemplateLoading] = useState<string | null>(null);
  // The template the user picked from the grid. Holding it in state
  // opens the details modal. Null means the modal is closed.
  const [selectedTemplate, setSelectedTemplate] = useState<SpecTemplate | null>(null);
  const [templateError, setTemplateError] = useState<string>("");
  const [undoDelete, setUndoDelete] = useState<{
    spec: Spec;
    timer: ReturnType<typeof setTimeout>;
  } | null>(null);
  // Spec paths the user has clicked delete on but whose 5s undo timer
  // has not yet fired. useUserActivity bumps the specs bus on every
  // keystroke/click/mouse move (throttled to 1/s), which triggers an
  // immediate fetchDocs. Without this guard, the next fetch hands back
  // the doc we just optimistically removed and the spec reappears for
  // the full ~5s window before the real DELETE finally removes it.
  const pendingDeleteSpecPathsRef = useRef<Set<string>>(new Set());
  const [showWizard, setShowWizard] = useState(false);
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [editingPath, setEditingPath] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");
  const [editSaving, setEditSaving] = useState(false);

  // Release-notes modal moved to a global watcher in Layout
  // (ReleaseNotesWatcher). Keeping the detection in this page meant
  // the modal only fired while /specs was mounted — Tori was missing
  // the celebration because she was watching the agents or chat when
  // the transition fired. The Layout-level watcher runs everywhere.

  // FastAPI's {doc_path:path} converter expects literal slashes. Encoding
  // the whole path with encodeURIComponent turns "/" into "%2F" and the
  // server then tries to open a file literally named "foo%2Fbar.md".
  // Encode each segment so special characters are safe but slashes stay.
  const encodeDocPath = (path: string) =>
    path.split("/").map(encodeURIComponent).join("/");

  const handleSaveBody = async (doc: Spec) => {
    setEditSaving(true);
    try {
      await api.patch(`/specs/${encodeDocPath(doc.path)}/body`, { body: editBody });
      setDocs((prev) =>
        prev.map((d) => (d.path === doc.path ? { ...d, body: editBody } : d))
      );
      setEditingPath(null);
      setMessage("Saved");
      setMessageType("success");
      setTimeout(() => setMessage(""), 2000);
    } catch {
      setMessage("Save failed");
      setMessageType("error");
    } finally {
      setEditSaving(false);
    }
  };

  const handleRename = async (doc: Spec) => {
    const trimmed = renameTitle.trim();
    setRenamingPath(null);
    if (!trimmed || trimmed === doc.title) return;
    try {
      await api.patch(`/specs/${encodeDocPath(doc.path)}/title`, { title: trimmed });
      setDocs((prev) =>
        prev.map((d) => (d.path === doc.path ? { ...d, title: trimmed } : d))
      );
    } catch (e) {
      reportError('Failed to rename spec', e);
    }
  };

  const deleteSpec = (spec: Spec) => {
    // Commit any previous pending delete immediately so the new one
    // can take over the toast slot.
    if (undoDelete) {
      clearTimeout(undoDelete.timer);
      api
        .delete(`/specs/${encodeDocPath(undoDelete.spec.path)}`)
        .catch(() => {});
      if (pendingDeleteSpecPathsRef.current.delete(undoDelete.spec.path)) {
        window.dispatchEvent(
          new CustomEvent("myos:pending-spec-delta", { detail: { delta: 1 } })
        );
      }
    }

    // Optimistically remove from the list.
    setDocs((prev) => prev.filter((d) => d.path !== spec.path));
    if (expandedPath === spec.path) setExpandedPath(null);
    pendingDeleteSpecPathsRef.current.add(spec.path);
    // Tell the Sidebar to subtract 1 from its cached spec count until
    // either the DELETE fires or the user hits Undo. Without this the
    // nav badge showed 1 while the Specs page already said 0 during
    // the 5 s undo window — a confusing split between two surfaces
    // that read the same fact.
    window.dispatchEvent(
      new CustomEvent("myos:pending-spec-delta", { detail: { delta: -1 } })
    );

    const timer = setTimeout(() => {
      api
        .delete(`/specs/${encodeDocPath(spec.path)}`)
        .catch((e) => reportError('Failed to delete spec', e))
        .finally(() => {
          if (pendingDeleteSpecPathsRef.current.delete(spec.path)) {
            // Cancel the -1 now that the server has caught up. The
            // DELETE response bumps the specs bus, which triggers a
            // fresh /specs/counts fetch in the sidebar.
            window.dispatchEvent(
              new CustomEvent("myos:pending-spec-delta", { detail: { delta: 1 } })
            );
          }
        });
      setUndoDelete(null);
    }, 5000);
    setUndoDelete({ spec, timer });
  };

  const handleUndoDeleteSpec = () => {
    if (!undoDelete) return;
    clearTimeout(undoDelete.timer);
    if (pendingDeleteSpecPathsRef.current.delete(undoDelete.spec.path)) {
      window.dispatchEvent(
        new CustomEvent("myos:pending-spec-delta", { detail: { delta: 1 } })
      );
    }
    setDocs((prev) => [...prev, undoDelete.spec]);
    setUndoDelete(null);
  };

  const fetchDocs = async () => {
    try {
      const data = await api.get<SpecsResponse>("/specs");
      const pending = pendingDeleteSpecPathsRef.current;
      const normalized = (data.docs || [])
        .filter((d) => pending.size === 0 || !pending.has(d.path))
        .map((d) => ({
          ...d,
          status: normalizeStatus(d.status),
        }));
      setDocs(normalized);
      writeSpecsCache(normalized);
    } catch {
      // API not available, keep empty state
    }
  };

  useEffect(() => {
    fetchDocs();
    // Check for URL parameter to expand a specific spec
    const params = new URLSearchParams(window.location.search);
    const expandParam = params.get('expand') || params.get('highlight');
    if (expandParam) {
      setExpandedPath(decodeURIComponent(expandParam));
    }
    // Refetch whenever any other surface (FilePreviewPane's "Make spec",
    // another tab, etc.) bumps the specs channel. This keeps the Specs
    // page in sync in real time without polling.
    const off = onSpecsChange(() => {
      fetchDocs();
    });
    return off;
  }, []);

  useEffect(() => {
    if (!focusParam) return;
    const path = decodeURIComponent(focusParam);
    const el = specRowRefs.current[path];
    if (!el) return;
    // Guard: scroll at most once per focus target so real-time doc refreshes
    // (onSpecsChange -> fetchDocs -> setDocs) do not re-fire this effect and
    // fight the user's own scroll position. (→1664)
    if (hasScrolledForFocusRef.current === path) return;
    hasScrolledForFocusRef.current = path;
    setTimeout(() => {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-2", "ring-emerald-400");
      setTimeout(() => el.classList.remove("ring-2", "ring-emerald-400"), 1500);
    }, 60);
  }, [focusParam, docs]);

  useEffect(() => {
    api
      .get<SpecTemplatesResponse>("/specs/templates")
      .then((res) => setTemplates(res.templates || []))
      .catch(() => setTemplates([]));
  }, []);

  // Clicking a template card no longer immediately calls the API. It
  // opens the details modal so the user can tweak the plan name and
  // add a short note before applying. Mirrors the agent-template flow
  // on the Agents page.
  const handleOpenTemplateDetails = (template: SpecTemplate) => {
    setTemplateError("");
    setSelectedTemplate(template);
  };

  const handleCloseTemplateDetails = () => {
    setSelectedTemplate(null);
    setTemplateError("");
  };

  // Called from the modal when the user clicks Create plan.
  const handleApplyTemplate = async (values: SpecTemplateDetailsValues) => {
    const template = selectedTemplate;
    if (!template) return;
    setTemplateLoading(template.id);
    setTemplateError("");
    try {
      const res = await api.post<{
        result: string;
        status?: string;
        promoted_path?: string | null;
      }>("/specs/from-template", {
        template_id: template.id,
        title: values.title,
        note: values.note,
      });
      await fetchDocs();
      // Open the new plan expanded so the user can see the pre-written
      // goal and acceptance criteria right away.
      const openPath = res.promoted_path || res.result;
      if (openPath) {
        setExpandedPath(openPath);
      }
      showMessage(`Spec created from "${template.name}". Ready to build.`);
      setSelectedTemplate(null);
    } catch {
      setTemplateError(
        `Could not create spec from "${template.name}". Try again.`,
      );
    } finally {
      setTemplateLoading(null);
    }
  };

  const showMessage = (text: string, type: "success" | "error" = "success") => {
    setMessage(text);
    setMessageType(type);
    setTimeout(() => setMessage(""), 4000);
  };


  const handlePromote = async (path: string) => {
    setLoading(true);
    try {
      const res = await api.post<{ result: string }>("/specs/promote", { path });
      await fetchDocs();
      showMessage(`Promoted to spec: ${res.result}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("checkbox")) {
        showMessage("This draft needs at least one checklist item (acceptance criteria) before it can be promoted.", "error");
      } else {
        showMessage("Could not promote draft. Make sure it has acceptance criteria.", "error");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleUnlock = async (path: string) => {
    setLoading(true);
    try {
      const encodedPath = path.split("/").map(encodeURIComponent).join("/");
      await api.post<{ result: string; path: string }>(`/specs/${encodedPath}/unlock`);
      await fetchDocs();
      showMessage("Spec reopened. You can now change the acceptance criteria.");
    } catch {
      showMessage("Could not reopen this spec. Try again.", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteOldHusks = async () => {
    const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const oldHusks = docs.filter(
      (d) => d.husk && d.created_at && new Date(d.created_at) < sevenDaysAgo
    );
    await Promise.allSettled(
      oldHusks.map((h) => api.delete(`/specs/${encodeDocPath(h.path)}`))
    );
    await fetchDocs();
    showMessage(`Deleted ${oldHusks.length} old ${oldHusks.length === 1 ? "empty draft" : "empty drafts"}.`);
  };

  // Wave 2: decompose is no longer user-facing. The /specs/{path}/build
  // endpoint auto-decomposes when the plan has no tasks yet, so there
  // is no standalone Break into Tasks button or handler anymore. The
  // decompose-success toast state remains so the auto-decompose path
  // inside Build could surface a count if we ever wire it through.

  const handleBuild = async (path: string) => {
    setBuildingSpec(path);
    // Optimistic: auto-expand the spec card and show a "Starting agents..."
    // placeholder row immediately so the user sees motion on the exact
    // card they clicked, not just a disabled button. The real task list
    // replaces this the moment the first /tasks poll returns (~500 ms).
    if (expandedPath !== path) {
      setExpandedPath(path);
    }
    setLinkedTasks((prev) => {
      if ((prev[path] || []).length > 0) return prev;
      return {
        ...prev,
        [path]: [{
          id: "starting",
          title: "Starting builder agents...",
          status: "open" as const,
          assigned_agent: null,
        }],
      };
    });
    try {
      // Shared helper. Internally POSTs /specs/{path}/build and converts
      // the kernel's 409 lock_conflict into a {status:'conflict'} result
      // instead of throwing, so we can surface it as a real error rather
      // than the generic "backend may not support this" toast.
      const result = await buildSpec(path);
      if (result.status === 'conflict') {
        const names = result.conflicts.map((c) => c.held_by_spawn).filter(Boolean);
        const who = names.length > 0 ? names.join(', ') : 'another agent';
        showMessage(`Cannot build: ${who} is editing files this build needs. Wait for it to finish, then try again.`, "error");
        setLinkedTasks((prev) => {
          const next = { ...prev };
          delete next[path];
          return next;
        });
        return;
      }
      await fetchDocs();
      const agents = result.agents;
      setBuildResult((prev) => ({
        ...prev,
        [path]: {
          agents,
          message: result.message,
          has_unchecked_acs: result.has_unchecked_acs,
        },
      }));
      if (agents.length > 0) {
        showMessage(`Started ${agents.length} agent${agents.length === 1 ? "" : "s"}. Watch the Agents tab.`);
        // The Agents page subscribes to the agents bus, so this bump
        // makes every brand-new builder row light up in the Active tab
        // within a single frame, well inside Tori's 1-second target.
        // Without this, the Active list would not refresh until the
        // Agents page's own 2 s poll tick, which looks sluggish for a
        // demo step Tori is trying to show live.
        bumpAgents();
        // Tasks bus: a brand-new task was just created per AC. Nudge
        // so any open Tasks page refreshes its list immediately.
        bumpTasks();
        // Auto-switch to the Building tab so the user lands on the
        // filtered list that contains the spec they just built. The
        // spec's status flips to "in-progress" on the backend when
        // agents spawn, so leaving the user on "all" or "drafts"
        // would hide the card they were just interacting with.
        setStageFilter("in_progress");
        // Fetch immediately so spinners and agent chips appear without
        // waiting for the first poll tick. Then fetch once more at
        // 500 ms to catch builder status changes that the backend
        // stamps right after spawn returns (assigned_agent, initial
        // open/closed flip). The 1 s recurring poll takes over after
        // that.
        fetchLinkedTasks(path);
        setTimeout(() => { fetchLinkedTasks(path); }, 500);
      } else {
        // Clear the optimistic "Starting..." placeholder on failure so
        // the card does not look stuck.
        setLinkedTasks((prev) => {
          const next = { ...prev };
          delete next[path];
          return next;
        });
        showMessage(result.message || "No open tasks to build.", "error");
      }
    } catch {
      setLinkedTasks((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
      showMessage("Could not start build. The backend may not support this yet.", "error");
    } finally {
      setBuildingSpec(null);
    }
  };

  const fetchLinkedTasks = async (path: string) => {
    try {
      const encodedPath = encodeURIComponent(path);
      const res = await api.get<SpecTasksResponse>(`/specs/${encodedPath}/tasks`);
      setLinkedTasks((prev) => ({ ...prev, [path]: res.tasks || [] }));
      setClaimsMap((prev) => ({ ...prev, [path]: res.claims || [] }));
    } catch {
      // Non-fatal: linked tasks are supplemental progress info.
    }
  };

  // Live progress polling. After a Build kicks off, poll the tasks
  // endpoint every 2 seconds so the spinner per row can flip to a
  // green checkmark as each agent closes its task. Stop polling once
  // every task is closed (nothing left to watch) or the spec card is
  // collapsed. One interval per building spec, keyed by doc.path.
  const pollTimersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  useEffect(() => {
    const activePaths = Object.keys(buildResult).filter(
      (p) => (buildResult[p]?.agents?.length ?? 0) > 0
    );
    for (const path of activePaths) {
      if (pollTimersRef.current[path]) continue;
      // Kick once so the rows show up immediately.
      fetchLinkedTasks(path);
      pollTimersRef.current[path] = setInterval(() => {
        fetchLinkedTasks(path);
        fetchDocs();
        // Cross-wake the Tasks page and the Agents page so any surface
        // that happens to be open picks up the closure within a tick
        // instead of waiting for its own independent poll cadence.
        // Cheap: bumpTasks and bumpAgents just fan out a no-data event
        // to local subscribers.
        bumpTasks();
        bumpAgents();
      }, 1000);
    }
    // Stop intervals for specs that are no longer building or whose
    // tasks are all closed.
    for (const path of Object.keys(pollTimersRef.current)) {
      const tasks = linkedTasks[path] || [];
      const allClosed = tasks.length > 0 && tasks.every((t) => t.status === "closed");
      const stillBuilding = activePaths.includes(path);
      if (!stillBuilding || allClosed) {
        clearInterval(pollTimersRef.current[path]);
        delete pollTimersRef.current[path];
      }
    }
    return () => {
      // Cleanup on unmount.
      for (const path of Object.keys(pollTimersRef.current)) {
        clearInterval(pollTimersRef.current[path]);
        delete pollTimersRef.current[path];
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildResult, linkedTasks]);

  const handleToggleExpand = (path: string) => {
    if (expandedPath === path) {
      setExpandedPath(null);
    } else {
      setExpandedPath(path);
      // Fetch linked tasks when expanding
      if (!linkedTasks[path]) {
        fetchLinkedTasks(path);
      }
    }
  };

  // Parse acceptance criteria from body if backend does not provide them
  const getAcceptanceCriteria = (doc: Spec): AcceptanceCriterion[] => {
    if (doc.acceptance_criteria && doc.acceptance_criteria.length > 0) {
      return doc.acceptance_criteria;
    }
    // Fallback: parse from body markdown
    const criteria: AcceptanceCriterion[] = [];
    if (!doc.body) return criteria;
    const lines = doc.body.split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("- [x] ")) {
        criteria.push({ text: trimmed.slice(6), checked: true });
      } else if (trimmed.startsWith("- [ ] ")) {
        criteria.push({ text: trimmed.slice(6), checked: false });
      }
    }
    return criteria;
  };

  // Clean up pending skeleton rows once the real spec they stand in for
  // shows up in docs. Match on promotedPath when the backend returned one,
  // otherwise on title. This is the hand-off: the skeleton vanishes in the
  // same render tick the real row appears, so there is never a duplicate.
  useEffect(() => {
    for (const pending of Object.values(pendingSpecsMap)) {
      if (pending.status === "error") continue;
      const match = docs.find(
        (d) =>
          (pending.promotedPath && d.path === pending.promotedPath) ||
          (!pending.promotedPath && d.title === pending.title)
      );
      if (match) removePendingSpec(pending.tempId);
    }
  }, [docs, pendingSpecsMap, removePendingSpec]);

  const pendingList = Object.values(pendingSpecsMap);

  // Stage-based filtering
  const stageFiltered =
    stageFilter === "all"
      ? docs
      : docs.filter((d) => getDocStage(d) === stageFilter);

  const stageCountByKey = (key: string) =>
    key === "all"
      ? docs.length
      : docs.filter((d) => getDocStage(d) === key).length;

  const stagePills: { key: StageFilter; label: string }[] = [
    { key: "all",         label: "All" },
    { key: "draft",       label: "Draft" },
    { key: "ready",       label: "Ready" },
    { key: "in_progress", label: "In Progress" },
    { key: "complete",    label: "Done" },
  ];

  // Husks in the current stage-filtered view
  const visibleHusks = stageFiltered.filter((d) => d.husk);

  return (
    <>
      {spawnGeminiSpec && (
        <SpawnGeminiModal
          path={spawnGeminiSpec.path}
          title={spawnGeminiSpec.title}
          checks={spawnGeminiSpec.checks}
          onClose={() => setSpawnGeminiSpec(null)}
          onSpawned={() => { setSpawnGeminiSpec(null); fetchDocs(); }}
        />
      )}
      {!embedded && <TopBar title="Specs" />}
      <div data-tour="specs" className={`px-4 pb-4 sm:px-8 sm:pb-8 max-w-6xl mx-auto`}>

        <SpecsOnboarding />
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold text-white">Specs</h1>
            <span className="bg-blue-500 text-white text-xs rounded-full px-2">
              {docs.length}
            </span>
          </div>
        </div>

        {/* Stage filter pills */}
        <div className="flex gap-1 mb-4 bg-white/60 dark:bg-slate-900/60 rounded-lg p-1 w-fit flex-wrap">
          {stagePills.map((p) => {
            const count = stageCountByKey(p.key);
            return (
              <button
                key={p.key}
                data-testid={`stage-filter-${p.key}`}
                onClick={() => setStageFilter(p.key)}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                  stageFilter === p.key
                    ? "bg-blue-500 text-white"
                    : "text-slate-600 dark:text-slate-400 hover:text-white"
                }`}
              >
                {p.label}
                {count > 0 && p.key !== "all" && (
                  <span className="ml-2 text-xs opacity-80">{count}</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Bulk empty-draft delete — appears when 2+ empty drafts are visible */}
        {visibleHusks.length >= 2 && (
          <div className="flex items-center gap-3 mb-4 px-3 py-2 bg-slate-50/40 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-700/50 rounded-lg w-fit">
            <span className="text-xs text-slate-600 dark:text-slate-400">
              {visibleHusks.length} empty drafts detected
            </span>
            <button
              type="button"
              data-testid="delete-all-husks-button"
              onClick={handleDeleteOldHusks}
              className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 font-medium"
            >
              Delete empty drafts older than 7 days
            </button>
          </div>
        )}

        {/* Create new draft */}
        <div className="flex gap-3 mb-8">
          <Button
            variant="primary"
            size="md"
            onClick={() => setShowWizard(true)}
          >
            New Spec
          </Button>
          <Button
            variant="secondary"
            size="md"
            onClick={() => navigate("/specs/import")}
          >
            Import
          </Button>
        </div>

        {showWizard && (
          <SpecWizard
            onComplete={(_path) => {
              setShowWizard(false);
              showMessage("Spec created from wizard!");
              fetchDocs();
            }}
            onCancel={() => setShowWizard(false)}
          />
        )}

        {/* Status message */}
        {message && (
          <div
            className={`text-sm rounded-lg px-4 py-2 mb-4 ${
              messageType === "success"
                ? "bg-green-500/20 text-green-600 dark:text-green-400"
                : "bg-red-500/20 text-red-600 dark:text-red-400"
            }`}
          >
            {message}
          </div>
        )}

        {/* Break into Tasks error banner */}
        {decomposeError && (
          <div className="mb-4" data-testid="decompose-error">
            <ErrorBanner message={decomposeError} />
          </div>
        )}

        {/* Pending skeleton rows (optimistic "Make spec" clicks).
            Rendered above the real list in the "all" and "drafts" tabs
            so the user arrives here from FilePreviewPane and immediately
            sees the in-flight spec instead of an empty list. Each row
            shows the initiative title, a shimmer, and a generating
            message so the wait feels grounded. */}
        {pendingList.length > 0 && (
          <div className="space-y-3 mb-3" data-testid="pending-specs-list">
            {pendingList.map((p) => (
              <div
                key={p.tempId}
                className="bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl p-5"
                data-testid="pending-spec-card"
                data-status={p.status}
              >
                <div className="flex items-center gap-3">
                  {p.status === "generating" && (
                    <span
                      className="block w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"
                      role="status"
                      aria-label="Generating"
                    />
                  )}
                  {p.status === "error" && (
                    <Icon name="error" className="text-red-600 dark:text-red-400" size={18} />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-white text-base font-medium truncate">
                      {p.title}
                    </p>
                    {p.status === "generating" && (
                      <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">
                        Generating acceptance criteria...
                      </p>
                    )}
                    {p.status === "error" && (
                      <p className="text-xs text-red-700 dark:text-red-300 mt-1">
                        {p.errorMsg || "Could not create the spec. Try again."}
                      </p>
                    )}
                  </div>
                  {p.status === "generating" && (
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-medium bg-slate-500/20 text-slate-700 dark:text-slate-300"
                      data-testid="pending-spec-status"
                    >
                      Generating...
                    </span>
                  )}
                  {p.status === "error" && (
                    <button
                      type="button"
                      onClick={() => removePendingSpec(p.tempId)}
                      className="text-xs text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 underline"
                      data-testid="dismiss-pending-spec"
                    >
                      Dismiss
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Spec list */}
        {stageFiltered.length === 0 && pendingList.length === 0 ? (
          <EmptyState
            icon="description"
            title="No specs yet"
            description="Create a draft to start planning."
          />
        ) : stageFiltered.length === 0 ? null : (
          <div className="space-y-3">
            {stageFiltered.map((doc) => {
              const isExpanded = expandedPath === doc.path;
              const criteria = getAcceptanceCriteria(doc);
              const tasks = linkedTasks[doc.path] || [];
              const hasTaskSummary = doc.task_summary && doc.task_summary.total > 0;

              return (
                <div
                  key={doc.path}
                  ref={(el) => { specRowRefs.current[doc.path] = el; }}
                  className="bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden"
                >
                  {/* Card header (always visible, clickable) */}
                  <div
                    className="p-5 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800/30 transition-colors"
                    onClick={() => handleToggleExpand(doc.path)}
                    data-testid="spec-card"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <Icon
                          name={isExpanded ? "expand_more" : "chevron_right"}
                          className="text-slate-500 flex-shrink-0"
                          size={20}
                        />
                        {doc.spec_id && (
                          <span
                            className="flex-shrink-0 font-mono text-xs font-medium text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded"
                            data-testid="spec-id-badge"
                          >
                            {doc.spec_id}
                          </span>
                        )}
                        {renamingPath === doc.path ? (
                          <input
                            autoFocus
                            data-testid="rename-spec-input"
                            className="text-white text-lg font-medium bg-transparent border-b border-blue-400 focus:outline-none min-w-0 flex-1"
                            value={renameTitle}
                            onChange={(e) => setRenameTitle(e.target.value)}
                            onBlur={(e) => { e.stopPropagation(); handleRename(doc); }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); handleRename(doc); }
                              if (e.key === "Escape") { e.stopPropagation(); setRenamingPath(null); }
                            }}
                            onClick={(e) => e.stopPropagation()}
                          />
                        ) : (
                          <p className="text-white text-lg font-medium truncate">
                            {doc.title}
                          </p>
                        )}
                        <StageChip
                          stage={getDocStage(doc)}
                          title={(() => {
                            const active = (claimsMap[doc.path] ?? []).filter(c => c.source !== 'build');
                            if (active.length === 0) return undefined;
                            const header = active.length === 1 ? '1 active claim:' : `${active.length} active claims:`;
                            const lines = active.map(c => `• ${c.agent} in ${c.source} · ${formatRelative(c.started_at)}`);
                            return [header, ...lines].join('\n');
                          })()}
                        />
                        {(claimsMap[doc.path] ?? [])
                          .filter((c) => c.source !== "build")
                          .map((c, i) => (
                            <ClaimSourceChip key={i} agent={c.agent} source={c.source} started_at={c.started_at} />
                          ))}
                        {doc.needs_clarity && (
                          <NeedsClarityChip
                            mode="spec"
                            checks={doc.clear_to_build_checks}
                            specPath={doc.path}
                            stage={getDocStage(doc)}
                          />
                        )}
                        {doc.husk && (
                          <span
                            className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/20 text-amber-600 dark:text-amber-400"
                            data-testid="husk-tag"
                            title={doc.husk_reason ?? "empty or placeholder draft"}
                          >
                            Empty draft
                          </span>
                        )}

                      </div>
                      <div className="flex items-center gap-4 flex-shrink-0">
                        {hasTaskSummary && (
                          <TaskProgressBar summary={doc.task_summary!} />
                        )}
                        {doc.created_at && (
                          <span className="text-slate-600 text-xs hidden sm:block">
                            {(() => {
                              const d = new Date(doc.created_at)
                              return isNaN(d.getTime()) ? '' : d.toLocaleDateString()
                            })()}
                          </span>
                        )}
                        <button
                          type="button"
                          aria-label="Rename spec"
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingPath(doc.path);
                            setRenameTitle(doc.title);
                          }}
                          data-testid="rename-spec-button"
                          className="text-slate-500 hover:text-white rounded-lg p-1 transition-colors"
                        >
                          <Icon name="edit" size={18} />
                        </button>
                        <button
                          type="button"
                          aria-label="Delete spec"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteSpec(doc);
                          }}
                          data-testid="delete-spec-button"
                          className="text-slate-500 hover:text-red-400 rounded-lg p-1 transition-colors"
                        >
                          <Icon name="delete" size={18} />
                        </button>
                      </div>
                    </div>

                    {/* Collapsed preview */}
                    {!isExpanded && doc.body && (
                      <p className="text-slate-600 dark:text-slate-400 text-sm mt-2 ml-8 line-clamp-1">
                        {doc.body.split("\n").find((l) => l.trim() && !l.trim().startsWith("#") && !l.trim().startsWith("- [")) || ""}
                      </p>
                    )}
                  </div>

                  {/* Expanded detail view */}
                  {isExpanded && (
                    <div className="border-t border-slate-200 dark:border-slate-800 px-5 pb-5" data-testid="spec-detail">
                      {/* Spec body — read mode or inline editor */}
                      <div className="mt-4">
                        {editingPath === doc.path ? (
                          <div className="space-y-2">
                            <textarea
                              data-testid="spec-body-textarea"
                              className="w-full h-64 bg-slate-100 dark:bg-slate-800 border border-slate-600 rounded-lg p-3 text-sm text-slate-800 dark:text-slate-200 font-mono resize-y focus:outline-none focus:border-blue-500"
                              value={editBody}
                              onChange={(e) => setEditBody(e.target.value)}
                              onClick={(e) => e.stopPropagation()}
                            />
                            <div className="flex gap-2">
                              <button
                                type="button"
                                data-testid="save-spec-body-button"
                                onClick={(e) => { e.stopPropagation(); handleSaveBody(doc); }}
                                disabled={editSaving}
                                className="bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1"
                              >
                                {editSaving ? "Saving..." : "Save"}
                              </button>
                              <button
                                type="button"
                                data-testid="cancel-spec-body-button"
                                onClick={(e) => { e.stopPropagation(); setEditingPath(null); }}
                                className="border border-slate-600 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div>
                            {doc.body && <SpecBody body={doc.body} />}
                            <button
                              type="button"
                              data-testid="edit-spec-body-button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditBody(doc.body || "");
                                setEditingPath(doc.path);
                              }}
                              className="mt-2 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
                            >
                              <Icon name="edit" size={14} />
                              Edit
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Acceptance criteria */}
                      {criteria.length > 0 && (
                        <AcceptanceCriteria criteria={criteria} />
                      )}

                      {/* Linked tasks */}
                      {tasks.length > 0 && (
                        <LinkedTasksList
                          tasks={tasks}
                          building={!!buildResult[doc.path] && tasks.some((t) => t.status === "open")}
                          onOpenAgent={(name) => navigate(`/agents?agent=${encodeURIComponent(name)}`)}
                        />
                      )}

                      {/* Path */}
                      <div className="flex items-center gap-2 text-xs text-slate-600 mt-4">
                        <Icon name="folder" className="text-sm" />
                        <span className="font-mono">{doc.path}</span>
                      </div>

                      {/* Guided next step + action buttons */}
                      <div className="mt-4 space-y-3">
                        {/* Step guidance */}
                        {doc.status === "draft" && ((doc.acceptance_criteria?.length ?? 0) > 0 || criteria.length > 0) && (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/20">
                            <Icon name="arrow_forward" size={16} className="text-green-600 dark:text-green-400" />
                            <p className="text-xs text-green-700 dark:text-green-300">
                              <span className="font-bold">Next step:</span> {(doc.acceptance_criteria?.length ?? 0) > 0 ? doc.acceptance_criteria!.length : criteria.length} acceptance criteria defined. Promote this draft to lock it in as a spec.
                            </p>
                          </div>
                        )}
                        {doc.status === "draft" && (doc.acceptance_criteria?.length ?? 0) === 0 && criteria.length === 0 && (
                          <div
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-50/40 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-700/50"
                            data-testid="no-criteria-indicator"
                          >
                            <Icon name="edit_note" size={14} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
                            <p className="text-xs text-slate-600 dark:text-slate-400">
                              No acceptance criteria yet. Add checklist items (<code className="text-slate-700 dark:text-slate-300">- [ ] ...</code>) to this draft to unlock promoting.
                            </p>
                          </div>
                        )}
                        {(doc.status === "ready" || doc.status === "spec" as string) && (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
                            <Icon name="arrow_forward" size={16} className="text-blue-600 dark:text-blue-400" />
                            <p className="text-xs text-blue-700 dark:text-blue-300">
                              <span className="font-bold">Next step:</span> Click "Build it" to turn this spec into tasks and spawn a builder agent for each one.
                            </p>
                          </div>
                        )}
                        {doc.status === "in-progress" && (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
                            <Icon name="arrow_forward" size={16} className="text-yellow-600 dark:text-yellow-400" />
                            <p className="text-xs text-yellow-700 dark:text-yellow-300">
                              <span className="font-bold">In progress:</span> Agents are working. The spec flips to Done automatically when every task closes.
                            </p>
                          </div>
                        )}
                        {doc.status === "complete" && (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/20">
                            <Icon name="check_circle" size={16} className="text-green-600 dark:text-green-400" />
                            <p className="text-xs text-green-700 dark:text-green-300">
                              <span className="font-bold">Done.</span> Every task closed and the feature is live.
                            </p>
                          </div>
                        )}

                        {/* Action buttons.
                            One Verify button, positioned consistently
                            regardless of status. Same label, same
                            tooltip, same endpoint. Disabled visual
                            state when a build is currently running. */}
                        {/* Gate clickability for buttons that would
                            otherwise produce a predictable error. Each
                            disabled branch has a tooltip explaining what
                            the user is waiting on, so the button does not
                            look broken. */}
                        {(() => {
                          // Promote is valid only when the draft has at
                          // least one acceptance-criterion checklist item.
                          // Trust the server array first, fall back to
                          // parsing "- [ ]" / "- [x]" lines out of the body
                          // so the UI flips to enabled the moment the
                          // checkboxes appear.
                          const parsedAc = getAcceptanceCriteria(doc);
                          const hasAc =
                            (doc.acceptance_criteria?.length ?? 0) > 0 ||
                            parsedAc.length > 0;
                          // If every in-progress task is closed, Build
                          // would surface "No open tasks to build". Gate
                          // the button's tooltip and disabled state on
                          // that instead of on the removed verify flow.
                          const totalTasks = doc.task_summary?.total ?? 0;
                          const openTasks = doc.task_summary?.open ?? 0;
                          const allClosed =
                            totalTasks > 0 && openTasks === 0;
                          return (
                            <div className="flex flex-wrap gap-2">
                              {doc.status === "draft" && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handlePromote(doc.path); }}
                                  disabled={loading || !hasAc}
                                  className="bg-green-500/20 text-green-600 dark:text-green-400 text-xs font-bold px-3 py-1.5 rounded-lg hover:bg-green-500/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                                  data-testid="promote-button"
                                  title={hasAc ? "Promote this draft to a spec." : "Waiting for acceptance criteria. This button unlocks once at least one checklist item appears."}
                                >
                                  <Icon name="verified" size={16} />
                                  Promote to Spec
                                </button>
                              )}
                              {(doc.status === "ready" || doc.status === "spec" as string) && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleBuild(doc.path); }}
                                  disabled={loading || buildingSpec === doc.path}
                                  className="bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold px-4 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                                  data-testid="build-button"
                                  title="Turns this spec into tasks and starts a builder agent for each one."
                                >
                                  <Icon name="rocket_launch" size={16} />
                                  {buildingSpec === doc.path ? "Building..." : "Build it"}
                                </button>
                              )}
                              {doc.status === "in-progress" && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleBuild(doc.path); }}
                                  disabled={loading || buildingSpec === doc.path || allClosed}
                                  className="bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold px-4 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                                  data-testid="build-button"
                                  title={allClosed ? "Every task is already closed. The spec is done." : "Turns this spec into tasks and starts a builder agent for each one."}
                                >
                                  <Icon name="rocket_launch" size={16} />
                                  {buildingSpec === doc.path ? "Building..." : "Build it"}
                                </button>
                              )}
                              {(doc.status === "ready" || doc.status === "spec" as string) && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleUnlock(doc.path); }}
                                  disabled={loading}
                                  className="border border-slate-600 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                                  data-testid="unlock-spec-button"
                                  title="Reopen this spec so you can change the acceptance criteria."
                                >
                                  <Icon name="lock_open" size={16} />
                                  Unlock and edit
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  window.open(`/api/specs/${doc.path}/export?format=speckit`);
                                }}
                                className="border border-slate-600 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5"
                                data-testid="export-spec-button"
                                title="Download this spec as a YAML file you can share or reimport."
                              >
                                <Icon name="download" size={16} />
                                Export YAML
                              </button>
                            </div>
                          );
                        })()}
                        <SpecDriveSync specPath={doc.path} />

                        {buildResult[doc.path] && buildResult[doc.path].agents.length > 0 && (
                          <div
                            className="mt-3 rounded-lg border border-blue-500/30 bg-blue-500/10 p-3 text-xs text-slate-800 dark:text-slate-200"
                            data-testid="build-result"
                          >
                            <div className="mb-1 font-semibold text-blue-700 dark:text-blue-300">
                              Started {buildResult[doc.path].agents.length} agent
                              {buildResult[doc.path].agents.length === 1 ? "" : "s"}.
                            </div>
                            <div className="mb-2 flex flex-wrap gap-1.5">
                              {buildResult[doc.path].agents.map((name) => (
                                <span
                                  key={name}
                                  className="rounded bg-slate-100 dark:bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-slate-700 dark:text-slate-300"
                                  data-testid="build-agent-name"
                                >
                                  {name}
                                </span>
                              ))}
                            </div>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                navigate("/agents");
                              }}
                              className="font-semibold text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
                              data-testid="build-agents-link"
                            >
                              Watch in the Agents tab -&gt;
                            </button>
                          </div>
                        )}
                        {buildResult[doc.path] &&
                          buildResult[doc.path].agents.length === 0 &&
                          buildResult[doc.path].has_unchecked_acs === false && (
                            <div
                              className="mt-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/40 dark:bg-slate-800/40 p-3 text-xs text-slate-700 dark:text-slate-300"
                              data-testid="build-no-acs-info"
                            >
                              This spec has no unchecked acceptance criteria. Add at least one and click Build.
                            </div>
                          )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Start from a template grid.
            Rendered after the spec list so the user sees their own plans
            first and only scrolls past them when they want to start a
            new one from scratch. */}
        {templates.length > 0 && (
          <div className="mt-8 mb-6" data-testid="plan-templates">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-3">
              Start from a template
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {templates.map((t) => {
                const loading = templateLoading === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => handleOpenTemplateDetails(t)}
                    disabled={loading}
                    data-testid={`plan-template-${t.id}`}
                    className="flex items-start gap-3 text-left bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl p-4 hover:border-blue-500 hover:bg-slate-50/40 dark:hover:bg-slate-800/40 transition-colors disabled:opacity-60 disabled:cursor-wait"
                  >
                    <Icon
                      name={t.icon || "description"}
                      className="text-2xl text-blue-600 dark:text-blue-400 mt-0.5 shrink-0"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-white text-sm font-medium">{t.name}</p>
                      <p className="text-slate-600 dark:text-slate-400 text-xs mt-0.5 line-clamp-2">
                        {t.description}
                      </p>
                      {loading && (
                        <p className="text-xs text-blue-700 dark:text-blue-300 mt-2">Creating spec...</p>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {undoDelete && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-sm text-slate-800 dark:text-slate-200 px-4 py-3 rounded-xl shadow-lg"
          data-testid="undo-delete-spec-toast"
        >
          <span>Spec deleted.</span>
          <button
            onClick={handleUndoDeleteSpec}
            className="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
            data-testid="undo-delete-spec-button"
          >
            Undo
          </button>
        </div>
      )}

      {decomposeToast && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-sm text-slate-800 dark:text-slate-200 px-4 py-3 rounded-xl shadow-lg"
          data-testid="decompose-success-toast"
        >
          <Icon name="check_circle" className="text-green-600 dark:text-green-400" size={18} />
          <span>
            Created {decomposeToast.count} {decomposeToast.count === 1 ? "task" : "tasks"}.
          </span>
          <button
            onClick={() => { setDecomposeToast(null); navigate("/tasks"); }}
            className="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
            data-testid="decompose-view-tasks-link"
          >
            View them →
          </button>
        </div>
      )}

      <SpecTemplateDetailsModal
        open={selectedTemplate !== null}
        template={selectedTemplate}
        submitting={
          selectedTemplate !== null && templateLoading === selectedTemplate.id
        }
        error={templateError}
        onClose={handleCloseTemplateDetails}
        onSubmit={handleApplyTemplate}
      />

    </>
  );
}
